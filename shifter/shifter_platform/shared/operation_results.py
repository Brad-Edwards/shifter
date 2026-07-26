"""Closed operation-result contract for the pause/resume + NGFW family.

ADR-043 phase 4 (#1836). ``shared.operation_envelope`` owns the *transport*
shape; this module owns the bounded, operation-specific ``payload`` that rides
inside it, plus the two things the transport deliberately does not model:

* **Step identity.** ``f"{operation_id}:{result_kind}"`` cannot identify a result
  when one operation emits several ``RESOURCE_STATE`` results — a range pause
  reports its instances, then its NGFW cascade, then its terminal state, and the
  cascade itself reports both ``pausing`` and ``paused``. Identity is
  therefore parameterized by a closed, deterministic per-operation step key. The
  same semantic step on retry reproduces the same key and the same digest; wall
  clock, thread-pool completion order, and delivery UUIDs are not ordering keys.
* **Legal order and terminality.** Declared per ``(resource, operation)`` so the
  applier can refuse a progress result that arrives after a terminal one instead
  of regressing domain state.

The payload is closed on keys: no ``**state_updates`` passthrough, no raw
Terraform/provider responses, no exception strings, no table column names, no
full state snapshots. Correlation is by UUID only — Engine integer primary keys
are never identity here.

Dependency-light on purpose: the standalone provisioner image imports this
without Django or the platform schema graph. There is one boundary error type,
reused from ``cyberscript``; callers do not add a parallel exception hierarchy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from cyberscript.enums import ResourceStatus
from cyberscript.exceptions import ValidationError as OperationResultError

__all__ = [
    "MAX_DIAGNOSTIC_CHARS",
    "MAX_INSTANCE_OUTCOMES",
    "NGFW_STATE_KEYS",
    "REASON_CODES",
    "OperationResultError",
    "ResultStep",
    "build_result_identity",
    "has_contract",
    "is_terminal_step",
    "latest_step",
    "parse_result_payload",
    "result_kind_for",
    "step_follows",
    "steps_for",
]


class ResultStep(StrEnum):
    """Closed, deterministic step keys. Scoped to a ``(resource, operation)``."""

    # range pause
    RANGE_INSTANCES_PAUSED = "range_instances_paused"
    RANGE_NGFW_CASCADE_PAUSING = "range_ngfw_cascade_pausing"
    RANGE_NGFW_CASCADE_PAUSED = "range_ngfw_cascade_paused"
    RANGE_TERMINAL_PAUSED = "range_terminal_paused"
    # range resume
    RANGE_NGFW_CASCADE_RESUMING = "range_ngfw_cascade_resuming"
    RANGE_NGFW_CASCADE_READY = "range_ngfw_cascade_ready"
    RANGE_INSTANCES_READY = "range_instances_ready"
    RANGE_TERMINAL_READY = "range_terminal_ready"
    # range, either direction
    RANGE_NGFW_CASCADE_FAILED = "range_ngfw_cascade_failed"
    RANGE_TERMINAL_FAILED = "range_terminal_failed"
    # ngfw provision: three observations all report ``provisioning``, so the step
    # key -- not the status -- is what distinguishes and orders them.
    NGFW_PROVISION_REQUESTED = "ngfw_provision_requested"
    NGFW_PROVISION_INFRA = "ngfw_provision_infra"
    NGFW_PROVISION_READY = "ngfw_provision_ready"
    # The auto-stop that ends provisioning is a step OF the provision generation,
    # not a second `stop` operation (ADR-043 phase 4 preflight).
    NGFW_PROVISION_AUTOSTOP = "ngfw_provision_autostop"
    # ngfw deprovision / power
    NGFW_DEPROVISION_DESTROYING = "ngfw_deprovision_destroying"
    NGFW_POWER_STARTING = "ngfw_power_starting"
    NGFW_POWER_STOPPING = "ngfw_power_stopping"
    # ngfw terminals
    NGFW_TERMINAL_READY = "ngfw_terminal_ready"
    NGFW_TERMINAL_PAUSED = "ngfw_terminal_paused"
    NGFW_TERMINAL_DESTROYED = "ngfw_terminal_destroyed"
    NGFW_TERMINAL_FAILED = "ngfw_terminal_failed"


class _Shape(StrEnum):
    """Which closed payload parser a step uses."""

    INSTANCES = "instances"
    NGFW = "ngfw"
    RANGE_TERMINAL = "range_terminal"
    FAILURE = "failure"


# Result kinds mirror ``engine.models.OperationResultKind`` without importing
# Django; the applier cross-checks the stored kind against this table.
_RESOURCE_STATE = "RESOURCE_STATE"
_TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
_TERMINAL_FAILURE = "TERMINAL_FAILURE"

# Bounded per ADR-043: results carry summaries, never snapshots.
MAX_INSTANCE_OUTCOMES = 256
MAX_DIAGNOSTIC_CHARS = 512

# Closed failure vocabulary. An authored code, never an exception string.
REASON_CODES = frozenset(
    {
        "cloud_operation_failed",
        "cloud_timeout",
        "dependency_unavailable",
        "invalid_state",
        "internal_error",
    }
)

# The provider-neutral NGFW state already shaped by
# ``ngfw_terraform_state._build_provider_state``. Raw Terraform output is not
# transported; only these normalized fields are.
NGFW_STATE_KEYS = frozenset(
    {
        "cloud_provider",
        "route_next_hop_ip",
        "attachment_mode",
        "data_attachment_id",
        "attached_ranges",
        "provider_metadata",
    }
)


@dataclass(frozen=True)
class _StepSpec:
    """Declared properties of one step within one ``(resource, operation)``."""

    rank: int
    result_kind: str
    shape: _Shape
    status: ResourceStatus | None
    terminal: bool = False


def _progress(rank: int, shape: _Shape, status: ResourceStatus) -> _StepSpec:
    return _StepSpec(rank=rank, result_kind=_RESOURCE_STATE, shape=shape, status=status)


def _success(rank: int, shape: _Shape, status: ResourceStatus) -> _StepSpec:
    return _StepSpec(rank=rank, result_kind=_TERMINAL_SUCCESS, shape=shape, status=status, terminal=True)


def _failure(rank: int) -> _StepSpec:
    return _StepSpec(rank=rank, result_kind=_TERMINAL_FAILURE, shape=_Shape.FAILURE, status=None, terminal=True)


_RANGE_PAUSE_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.RANGE_INSTANCES_PAUSED: _progress(10, _Shape.INSTANCES, ResourceStatus.PAUSED),
    ResultStep.RANGE_NGFW_CASCADE_PAUSING: _progress(20, _Shape.NGFW, ResourceStatus.PAUSING),
    ResultStep.RANGE_NGFW_CASCADE_PAUSED: _progress(30, _Shape.NGFW, ResourceStatus.PAUSED),
    ResultStep.RANGE_NGFW_CASCADE_FAILED: _progress(30, _Shape.NGFW, ResourceStatus.FAILED),
    ResultStep.RANGE_TERMINAL_PAUSED: _success(40, _Shape.RANGE_TERMINAL, ResourceStatus.PAUSED),
    ResultStep.RANGE_TERMINAL_FAILED: _failure(40),
}

_RANGE_RESUME_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.RANGE_NGFW_CASCADE_RESUMING: _progress(10, _Shape.NGFW, ResourceStatus.RESUMING),
    ResultStep.RANGE_NGFW_CASCADE_READY: _progress(20, _Shape.NGFW, ResourceStatus.READY),
    ResultStep.RANGE_NGFW_CASCADE_FAILED: _progress(20, _Shape.NGFW, ResourceStatus.FAILED),
    ResultStep.RANGE_INSTANCES_READY: _progress(30, _Shape.INSTANCES, ResourceStatus.READY),
    ResultStep.RANGE_TERMINAL_READY: _success(40, _Shape.RANGE_TERMINAL, ResourceStatus.READY),
    ResultStep.RANGE_TERMINAL_FAILED: _failure(40),
}

_NGFW_PROVISION_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.NGFW_PROVISION_REQUESTED: _progress(10, _Shape.NGFW, ResourceStatus.PROVISIONING),
    ResultStep.NGFW_PROVISION_INFRA: _progress(20, _Shape.NGFW, ResourceStatus.PROVISIONING),
    ResultStep.NGFW_PROVISION_READY: _progress(30, _Shape.NGFW, ResourceStatus.READY),
    # Provisioning ends paused: the NGFW is auto-stopped once it is ready.
    ResultStep.NGFW_PROVISION_AUTOSTOP: _success(40, _Shape.NGFW, ResourceStatus.PAUSED),
    ResultStep.NGFW_TERMINAL_FAILED: _failure(40),
}

_NGFW_DEPROVISION_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.NGFW_DEPROVISION_DESTROYING: _progress(10, _Shape.NGFW, ResourceStatus.DESTROYING),
    ResultStep.NGFW_TERMINAL_DESTROYED: _success(20, _Shape.NGFW, ResourceStatus.DESTROYED),
    ResultStep.NGFW_TERMINAL_FAILED: _failure(20),
}

_NGFW_START_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.NGFW_POWER_STARTING: _progress(10, _Shape.NGFW, ResourceStatus.RESUMING),
    ResultStep.NGFW_TERMINAL_READY: _success(20, _Shape.NGFW, ResourceStatus.READY),
    ResultStep.NGFW_TERMINAL_FAILED: _failure(20),
}

_NGFW_STOP_STEPS: dict[ResultStep, _StepSpec] = {
    ResultStep.NGFW_POWER_STOPPING: _progress(10, _Shape.NGFW, ResourceStatus.PAUSING),
    ResultStep.NGFW_TERMINAL_PAUSED: _success(20, _Shape.NGFW, ResourceStatus.PAUSED),
    ResultStep.NGFW_TERMINAL_FAILED: _failure(20),
}

# ``aces-range`` shares the range lifecycle contract; the applier resolves the
# target differently, the result shape is the same.

_CONTRACT: dict[tuple[str, str], dict[ResultStep, _StepSpec]] = {
    ("range", "pause"): _RANGE_PAUSE_STEPS,
    ("range", "resume"): _RANGE_RESUME_STEPS,
    ("aces-range", "pause"): _RANGE_PAUSE_STEPS,
    ("aces-range", "resume"): _RANGE_RESUME_STEPS,
    ("ngfw", "provision"): _NGFW_PROVISION_STEPS,
    ("ngfw", "deprovision"): _NGFW_DEPROVISION_STEPS,
    ("ngfw", "start"): _NGFW_START_STEPS,
    ("ngfw", "stop"): _NGFW_STOP_STEPS,
}


def _steps(resource: str, operation: str) -> dict[ResultStep, _StepSpec]:
    """Return the declared step table for a pair, or fail closed."""
    try:
        return _CONTRACT[(resource, operation)]
    except KeyError:
        raise OperationResultError(f"no result contract for resource '{resource}' operation '{operation}'") from None


def _spec(resource: str, operation: str, step: ResultStep | str) -> _StepSpec:
    """Return the spec for a step declared on this pair, or fail closed."""
    table = _steps(resource, operation)
    try:
        declared = ResultStep(step)
    except ValueError:
        raise OperationResultError(f"unknown result step '{step}'") from None
    spec = table.get(declared)
    if spec is None:
        raise OperationResultError(f"result step '{declared}' is not declared for {resource}:{operation}")
    return spec


def steps_for(resource: str, operation: str) -> frozenset[ResultStep]:
    """Return every step key declared for a ``(resource, operation)``."""
    return frozenset(_steps(resource, operation))


def has_contract(resource: str, operation: str) -> bool:
    """Return True when this family has an authoritative result contract.

    Families still on the shadow path (those not yet cut over from direct
    provisioner SQL) have no contract here; the applier leaves them in shadow
    rather than rejecting them.
    """
    return (resource, operation) in _CONTRACT


def result_kind_for(resource: str, operation: str, *, step: ResultStep | str) -> str:
    """Return the result kind a step must be recorded under."""
    return _spec(resource, operation, step).result_kind


def is_terminal_step(resource: str, operation: str, *, step: ResultStep | str) -> bool:
    """Return True when the step terminates its operation generation."""
    return _spec(resource, operation, step).terminal


def step_follows(
    resource: str,
    operation: str,
    *,
    previous: ResultStep | str | None,
    step: ResultStep | str,
) -> bool:
    """Return True when ``step`` may legally be applied after ``previous``.

    ``previous`` is the last step already applied for this operation generation,
    or None when nothing has been applied yet. Once a terminal step is applied
    only its own replay is legal: a late progress result must never regress
    domain state, and a late failure must never overwrite a terminal success.
    """
    spec = _spec(resource, operation, step)
    if previous is None:
        return True
    previous_spec = _spec(resource, operation, previous)
    if previous_spec.terminal:
        return ResultStep(step) == ResultStep(previous)
    return spec.rank >= previous_spec.rank


def latest_step(resource: str, operation: str, steps: Iterable[ResultStep | str]) -> ResultStep | None:
    """Return the furthest-advanced step among ``steps``, or None when empty.

    The applier passes the steps already applied for one operation generation and
    uses the result as ``previous`` for the ordering check, so out-of-order
    delivery is judged against the high-water mark rather than the last row
    written.
    """
    ranked = [(_spec(resource, operation, step).rank, ResultStep(step)) for step in steps]
    if not ranked:
        return None
    # Terminal steps outrank their same-rank peers so a terminal already applied
    # is never masked by a progress step recorded at the same rank.
    return max(ranked, key=lambda item: (item[0], _spec(resource, operation, item[1]).terminal))[1]


def build_result_identity(*, operation_id: str | UUID, step: ResultStep | str, digest: str) -> str:
    """Return the deterministic inbox identity for one result.

    The digest is part of the identity so the append boundary can absorb an
    identical replay with ``ON CONFLICT DO NOTHING`` while a *conflicting* replay
    lands as a second row for the same step — detectable by the applier, which
    may read the inbox, without granting the provisioner any inbox read
    (migration 0036 grants INSERT only).
    """
    return f"{operation_id}:{ResultStep(step)}:{digest}"


def _require_dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OperationResultError(f"{field} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], allowed: frozenset[str], field: str) -> None:
    actual = frozenset(value)
    unexpected = sorted(actual - allowed)
    if unexpected:
        raise OperationResultError(f"{field} has unexpected field(s): {', '.join(unexpected)}")
    missing = sorted(allowed - actual)
    if missing:
        raise OperationResultError(f"{field} is missing field(s): {', '.join(missing)}")


def _require_uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise OperationResultError(f"{field} must be a UUID string")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise OperationResultError(f"{field} must be a valid UUID") from exc


def _require_status(value: object, expected: ResourceStatus | None, field: str) -> str:
    if not isinstance(value, str):
        raise OperationResultError(f"{field} must be a status string")
    try:
        status = ResourceStatus(value)
    except ValueError:
        raise OperationResultError(f"{field} is not a known resource status") from None
    if expected is not None and status != expected:
        raise OperationResultError(f"{field} must be '{expected.value}' for this step, got '{status.value}'")
    return status.value


def _parse_instances(payload: dict[str, Any], spec: _StepSpec) -> dict[str, Any]:
    _require_exact_keys(payload, frozenset({"instances"}), "result payload")
    raw = payload["instances"]
    if not isinstance(raw, list):
        raise OperationResultError("result payload instances must be a list")
    if len(raw) > MAX_INSTANCE_OUTCOMES:
        raise OperationResultError(f"result payload carries more than {MAX_INSTANCE_OUTCOMES} instance outcomes")
    outcomes = []
    for index, item in enumerate(raw):
        entry = _require_dict(item, f"result payload instances[{index}]")
        _require_exact_keys(entry, frozenset({"instance_uuid", "status"}), f"result payload instances[{index}]")
        outcomes.append(
            {
                "instance_uuid": _require_uuid(entry["instance_uuid"], f"result payload instances[{index}] uuid"),
                "status": _require_status(entry["status"], spec.status, f"result payload instances[{index}] status"),
            }
        )
    return {"instances": outcomes}


def _parse_ngfw_state(value: object) -> dict[str, Any]:
    state = _require_dict(value, "result payload ngfw_state")
    unexpected = sorted(frozenset(state) - NGFW_STATE_KEYS)
    if unexpected:
        raise OperationResultError(f"result payload ngfw_state has unexpected field(s): {', '.join(unexpected)}")
    return dict(state)


def _parse_ngfw(payload: dict[str, Any], spec: _StepSpec) -> dict[str, Any]:
    required = frozenset({"ngfw_instance_uuid", "status"})
    unexpected = sorted(frozenset(payload) - (required | {"ngfw_state"}))
    if unexpected:
        raise OperationResultError(f"result payload has unexpected field(s): {', '.join(unexpected)}")
    missing = sorted(required - frozenset(payload))
    if missing:
        raise OperationResultError(f"result payload is missing field(s): {', '.join(missing)}")
    parsed: dict[str, Any] = {
        "ngfw_instance_uuid": _require_uuid(payload["ngfw_instance_uuid"], "result payload ngfw_instance_uuid"),
        "status": _require_status(payload["status"], spec.status, "result payload status"),
    }
    if "ngfw_state" in payload:
        parsed["ngfw_state"] = _parse_ngfw_state(payload["ngfw_state"])
    return parsed


def _parse_range_terminal(payload: dict[str, Any], spec: _StepSpec) -> dict[str, Any]:
    _require_exact_keys(payload, frozenset({"status"}), "result payload")
    return {"status": _require_status(payload["status"], spec.status, "result payload status")}


def _parse_failure(payload: dict[str, Any], _spec_unused: _StepSpec) -> dict[str, Any]:
    _require_exact_keys(payload, frozenset({"reason_code", "diagnostic"}), "result payload")
    reason_code = payload["reason_code"]
    if not isinstance(reason_code, str) or reason_code not in REASON_CODES:
        raise OperationResultError(f"result payload reason_code must be one of: {', '.join(sorted(REASON_CODES))}")
    diagnostic = payload["diagnostic"]
    if not isinstance(diagnostic, str):
        raise OperationResultError("result payload diagnostic must be a string")
    if len(diagnostic) > MAX_DIAGNOSTIC_CHARS:
        raise OperationResultError(f"result payload diagnostic exceeds {MAX_DIAGNOSTIC_CHARS} characters")
    return {"reason_code": reason_code, "diagnostic": diagnostic}


_PARSERS = {
    _Shape.INSTANCES: _parse_instances,
    _Shape.NGFW: _parse_ngfw,
    _Shape.RANGE_TERMINAL: _parse_range_terminal,
    _Shape.FAILURE: _parse_failure,
}


def parse_result_payload(
    resource: str,
    operation: str,
    *,
    step: ResultStep | str,
    payload: object,
) -> dict[str, Any]:
    """Validate a result payload against its step's closed shape.

    Returns the normalized payload. Fails closed on an undeclared step, an
    unexpected or missing field, a non-UUID correlation id, an unknown status, a
    status incoherent with the step, an unbounded collection, or an unauthored
    failure reason.
    """
    spec = _spec(resource, operation, step)
    obj = _require_dict(payload, "result payload")
    return _PARSERS[spec.shape](obj, spec)
