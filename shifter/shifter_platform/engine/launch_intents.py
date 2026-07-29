"""Validated durable intents for privileged provisioner Job launches."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Model, QuerySet
from django.utils import timezone

from engine.models import (
    Instance,
    InterruptState,
    OperationInput,
    ProvisionerLaunchIntent,
    ProvisionerLaunchStatus,
    Range,
    Request,
)
from engine.operation_inputs import operation_input_payload
from shared.cloud import PROVISIONER_CONTAINER_NAME
from shared.cloud.gcp.base import build_idempotent_job_name
from shared.operation_envelope import build_operation_envelope

_OPERATIONS = {
    "range": {"provision", "destroy", "pause", "resume"},
    "raes-range": {"provision", "destroy", "pause", "resume"},
    "ngfw": {"provision", "deprovision", "start", "stop"},
}
PROVISIONER_DISPATCH_FAILED = "Provisioner dispatch failed"

# Bounded convergence budget for a provision-task interrupt (#277). Safe internal
# default; promote to a typed deployment setting only if operators need to tune it.
_INTERRUPT_DEADLINE_SECONDS = 1800
# Only the RAES provision generation is interruptible in this scope (#277); the
# AWS legacy ``range`` provision path is #1894.
_INTERRUPTIBLE_PROVISION = ("raes-range", "provision")


def _request_payload(command: list[str]) -> dict[str, object] | None:
    """Validate and normalize a request-id command when its shape matches."""
    if len(command) != 4 or command[2] != "--request-id":
        return None
    resource, operation, _, request_id = command
    if resource not in _OPERATIONS or operation not in _OPERATIONS[resource]:
        raise ValueError("unsupported provisioner resource or operation")
    try:
        parsed_request_id = UUID(request_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("request_id must be a UUID") from exc
    return {
        "version": 1,
        "resource": resource,
        "operation": operation,
        "request_id": str(parsed_request_id),
    }


def _legacy_range_payload(command: list[str]) -> dict[str, object] | None:
    """Validate and normalize a legacy range command when its shape matches."""
    if len(command) != 6 or command[:2] not in (["range", "provision"], ["range", "destroy"]):
        return None
    if command[2] != "--range-id" or command[4] != "--user-id":
        raise ValueError("legacy range command must use canonical identifier flags")
    try:
        range_id, user_id = int(command[3]), int(command[5])
    except ValueError as exc:
        raise ValueError("legacy identifiers must be integers") from exc
    if range_id < 0 or user_id < 0:
        raise ValueError("legacy identifiers must be non-negative")
    return {
        "version": 1,
        "resource": "range",
        "operation": command[1],
        "range_id": range_id,
        "user_id": user_id,
    }


def _split_operation_id(command: list[str]) -> tuple[list[str], str | None]:
    """Split an optional trailing ``--operation-id <uuid>`` correlation pair.

    The generation fence (``operation_id``) is carried on the launched argv so the
    provisioner tags its input read and result appends with exactly the operation
    it is executing, never "latest by request" (ADR-043). It is optional so the
    engine can validate the canonical command before the id is minted, and add it
    only when reconstructing the dispatch argv from a persisted intent.
    """
    if len(command) >= 2 and command[-2] == "--operation-id":
        try:
            operation_id = str(UUID(command[-1]))
        except (TypeError, ValueError) as exc:
            raise ValueError("operation_id must be a UUID") from exc
        return command[:-2], operation_id
    return command, None


def validate_provisioner_command(command: list[str]) -> dict[str, object]:
    """Return a versioned, secret-free payload for one canonical CLI command."""
    if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
        raise ValueError("provisioner command must be a list of strings")
    base, operation_id = _split_operation_id(command)
    payload = _request_payload(base) or _legacy_range_payload(base)
    if payload is None:
        raise ValueError("command does not match a canonical provisioner launch shape")
    if operation_id is not None:
        payload["operation_id"] = operation_id
    return payload


def command_from_payload(payload: dict[str, object]) -> list[str]:
    """Reconstruct and revalidate the canonical command at the trust boundary."""
    if payload.get("version") != 1:
        raise ValueError("unsupported provisioner launch intent version")
    resource = payload.get("resource")
    operation = payload.get("operation")
    if "request_id" in payload:
        command = [str(resource), str(operation), "--request-id", str(payload["request_id"])]
    else:
        command = [
            str(resource),
            str(operation),
            "--range-id",
            str(payload.get("range_id")),
            "--user-id",
            str(payload.get("user_id")),
        ]
    if payload.get("operation_id"):
        command = [*command, "--operation-id", str(payload["operation_id"])]
    if validate_provisioner_command(command) != payload:
        raise ValueError("provisioner launch intent payload is not canonical")
    return command


def _lock_for_generation[ModelT: Model](
    queryset: QuerySet[ModelT], expected_operation_id: UUID | str | None
) -> QuerySet[ModelT]:
    """Lock a domain projection when validating a persisted operation generation."""
    return queryset.select_for_update() if expected_operation_id is not None else queryset


def _require_current_generation(row: Range | Instance, expected_operation_id: UUID | str | None) -> None:
    """Reject an intent that belongs to a superseded domain-operation generation."""
    if expected_operation_id is not None and row.provisioner_operation_id != UUID(str(expected_operation_id)):
        raise ValueError("launch intent operation generation is no longer current")


def _authorize_legacy_range(
    payload: dict[str, object],
    target: Range | Instance | None,
    expected_operation_id: UUID | str | None,
) -> None:
    """Authorize a legacy range-id/user-id payload."""
    range_rows = _lock_for_generation(Range.objects.select_related("user"), expected_operation_id)
    row = target if isinstance(target, Range) else range_rows.filter(pk=int(str(payload.get("range_id")))).first()
    if row is None or row.user_id != payload.get("user_id"):
        raise ValueError("legacy launch intent does not match an owned range")
    _require_current_generation(row, expected_operation_id)
    allowed_states = {
        "provision": {Range.Status.PENDING, Range.Status.PROVISIONING},
        "destroy": {Range.Status.DESTROYING},
    }
    if row.status not in allowed_states[str(payload.get("operation"))]:
        raise ValueError("range state does not authorize the requested operation")


def _authorize_request_range(
    payload: dict[str, object],
    request: Request,
    target: Range | Instance | None,
    expected_operation_id: UUID | str | None,
) -> None:
    """Authorize a request-based Range or RAES Range payload."""
    range_rows = _lock_for_generation(Range.objects.all(), expected_operation_id)
    row = target if isinstance(target, Range) else range_rows.filter(request=request).first()
    if row is None:
        raise ValueError("launch intent request has no range")
    _require_current_generation(row, expected_operation_id)
    allowed_states = {
        "provision": {Range.Status.PENDING, Range.Status.PROVISIONING},
        "destroy": {Range.Status.DESTROYING},
        "pause": {Range.Status.PAUSING},
        "resume": {Range.Status.RESUMING},
    }
    if row.status not in allowed_states[str(payload.get("operation"))]:
        raise ValueError("range state does not authorize the requested operation")


def _authorize_request_ngfw(
    payload: dict[str, object],
    request: Request,
    target: Range | Instance | None,
    expected_operation_id: UUID | str | None,
) -> None:
    """Authorize a request-based NGFW payload."""
    ngfw_rows = _lock_for_generation(Instance.objects.all(), expected_operation_id)
    row = target if isinstance(target, Instance) else ngfw_rows.filter(request=request, role=Instance.Role.NGFW).first()
    if row is None:
        raise ValueError("launch intent request has no NGFW instance")
    _require_current_generation(row, expected_operation_id)
    allowed_states = {
        "provision": {"pending", "provisioning"},
        "deprovision": {"ready", "paused", "failed"},
        "start": {"paused", "failed"},
        "stop": {"ready"},
    }
    if row.status not in allowed_states[str(payload.get("operation"))]:
        raise ValueError("NGFW state does not authorize the requested operation")


def authorize_provisioner_payload(
    payload: dict[str, object],
    *,
    target: Range | Instance | None = None,
    expected_operation_id: UUID | str | None = None,
) -> None:
    """Fail closed unless current domain state authorizes the queued operation."""
    if "request_id" not in payload:
        _authorize_legacy_range(payload, target, expected_operation_id)
        return
    request = Request.objects.filter(request_id=UUID(str(payload["request_id"]))).first()
    if request is None:
        raise ValueError("launch intent request does not exist")
    if payload.get("resource") in {"range", "raes-range"}:
        _authorize_request_range(payload, request, target, expected_operation_id)
    else:
        _authorize_request_ngfw(payload, request, target, expected_operation_id)


def _lock_operation_target(payload: dict[str, object]) -> Range | Instance:
    """Lock and return the domain row that owns an operation generation."""
    if "request_id" not in payload:
        return Range.objects.select_for_update().get(pk=int(str(payload["range_id"])))
    request = Request.objects.get(request_id=UUID(str(payload["request_id"])))
    if payload["resource"] in {"range", "raes-range"}:
        return Range.objects.select_for_update().get(request=request)
    return Instance.objects.select_for_update().get(request=request, role=Instance.Role.NGFW)


def _should_rotate_generation(row: Range | Instance, operation: str) -> bool:
    """Return whether the domain row needs a fresh operation generation."""
    if row.provisioner_operation != operation or row.provisioner_operation_id is None:
        return True
    intent = ProvisionerLaunchIntent.objects.filter(operation_id=row.provisioner_operation_id).first()
    return intent is not None and (
        intent.status == ProvisionerLaunchStatus.DLQ
        or (intent.status == ProvisionerLaunchStatus.SUCCEEDED and row.status == "failed")
    )


def _operation_identity(payload: dict[str, object]) -> UUID:
    """Return the stable identity of the current authorized domain operation."""
    operation = f"{payload['resource']}:{payload['operation']}"
    with transaction.atomic():
        row = _lock_operation_target(payload)
        authorize_provisioner_payload(payload, target=row)
        if _should_rotate_generation(row, operation):
            row.provisioner_operation = operation
            row.provisioner_operation_id = uuid4()
            row.save(update_fields=["provisioner_operation", "provisioner_operation_id"])
        operation_id = row.provisioner_operation_id
        assert operation_id is not None, "operation generation must be reserved"
        return operation_id


def request_provision_interrupt(range_obj: Range) -> bool:
    """Record a durable interrupt against the range's current provision generation (#277).

    Bound under the caller's transaction to the current ``provisioner_operation_id``
    -- never keyed by ``request_id`` or task reference alone. Scoped to the RAES
    provision path; the AWS legacy ``range`` path is out of scope (#1894). The
    launcher worker converges the recorded request (suppress pending / stop running
    / observe terminal absence / enqueue canonical destroy). Idempotent: returns
    True without re-stamping when the generation is already marked.

    Returns:
        True when an interruptible provision generation was (or already is) marked.
    """
    op_id = range_obj.provisioner_operation_id
    if op_id is None:
        return False
    intent = ProvisionerLaunchIntent.objects.select_for_update().filter(operation_id=op_id).first()
    if intent is None:
        return False
    payload = intent.payload or {}
    if (payload.get("resource"), payload.get("operation")) != _INTERRUPTIBLE_PROVISION:
        return False
    if intent.interrupt_state != InterruptState.NONE:
        return True
    now = timezone.now()
    intent.interrupt_state = InterruptState.REQUESTED
    intent.interrupt_requested_at = now
    intent.interrupt_next_attempt_at = now
    intent.interrupt_deadline = now + timedelta(seconds=_INTERRUPT_DEADLINE_SECONDS)
    intent.save(
        update_fields=[
            "interrupt_state",
            "interrupt_requested_at",
            "interrupt_next_attempt_at",
            "interrupt_deadline",
        ]
    )
    return True


def clear_provisioner_operation_after_failure(row: Range | Instance) -> list[str]:
    """Close a failed lifecycle episode so the same operation can be retried."""
    if row.provisioner_operation_id is None and not row.provisioner_operation:
        return []
    row.provisioner_operation = ""
    row.provisioner_operation_id = None
    return ["provisioner_operation", "provisioner_operation_id"]


def _resolve_failure_target(payload: dict[str, object]) -> Range | Instance | None:
    """Lock the domain row named by a validated failure payload."""
    target: Range | Instance | None
    if "request_id" not in payload:
        target = Range.objects.select_for_update().filter(pk=int(str(payload["range_id"]))).first()
    else:
        request = Request.objects.filter(request_id=UUID(str(payload["request_id"]))).first()
        if request is None:
            target = None
        elif payload.get("resource") in {"range", "raes-range"}:
            target = Range.objects.select_for_update().filter(request=request).first()
        else:
            target = Instance.objects.select_for_update().filter(request=request, role=Instance.Role.NGFW).first()
    return target


def _generation_still_authorizes_failure(
    payload: dict[str, object],
    target: Range | Instance,
    expected_operation_id: UUID | str,
) -> bool:
    """Return whether a provider failure still owns the current lifecycle."""
    try:
        authorize_provisioner_payload(
            payload,
            target=target,
            expected_operation_id=expected_operation_id,
        )
    except ValueError:
        return False
    return True


def _publish_range_dispatch_failure(payload: dict[str, object], target: Range) -> None:
    """Publish the standard failed status event for a Range dispatch."""
    from engine.models import RangeEventOutbox
    from shared.enums import ResourceStatus
    from shared.messages.events import EVENT_TYPE_STATUS_UPDATED

    event_id = uuid4()
    related_request = target.request
    request_id = str(related_request.request_id) if related_request is not None else str(payload.get("request_id", ""))
    event = {
        "event_type": EVENT_TYPE_STATUS_UPDATED,
        "event_id": str(event_id),
        "timestamp": timezone.now().isoformat(),
        "request_id": request_id,
        "range_id": target.id,
        "user_id": target.user_id,
        "new_status": ResourceStatus.FAILED.value,
        "error_message": PROVISIONER_DISPATCH_FAILED,
    }
    RangeEventOutbox.objects.create(
        event_id=event_id,
        event_type=EVENT_TYPE_STATUS_UPDATED,
        payload=event,
        next_attempt_at=timezone.now(),
    )


def _apply_dispatch_failure(payload: dict[str, object], target: Range | Instance) -> None:
    """Persist the sanitized failure state and its dependent projections."""
    from engine.models import App
    from shared.enums import ResourceStatus

    target.status = ResourceStatus.FAILED.value
    update_fields = ["status", "updated_at"]
    update_fields.extend(clear_provisioner_operation_after_failure(target))
    if isinstance(target, Range):
        target.error_message = PROVISIONER_DISPATCH_FAILED
        update_fields.append("error_message")
    target.save(update_fields=update_fields)
    if isinstance(target, Range):
        _publish_range_dispatch_failure(payload, target)
    else:
        App.objects.filter(instance=target).update(
            status=ResourceStatus.FAILED.value,
            updated_at=timezone.now(),
        )


def fail_current_provisioner_operation(
    payload: dict[str, object],
    expected_operation_id: UUID | str,
) -> bool:
    """Fail only the domain projection still owned by this operation generation."""
    try:
        command_from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return False
    target = _resolve_failure_target(payload)
    if target is None or not _generation_still_authorizes_failure(payload, target, expected_operation_id):
        return False
    _apply_dispatch_failure(payload, target)
    return True


def _materialize_operation_input(payload: dict[str, object], operation_id: UUID) -> None:
    """Persist the immutable operation input keyed by ``operation_id``.

    Runs inside the launch-intent transaction so the input and intent commit
    atomically (ADR-043). The provisioner reads exactly this row by
    ``operation_id``. Immutable: created once per operation generation.
    """
    target = _lock_operation_target(payload)
    request: Request | None = getattr(target, "request", None)
    request_id = getattr(request, "request_id", None)
    if request is None or request_id is None:
        # Deprecated legacy range with no linked request: no request-keyed input
        # projection to materialize in shadow. Skip rather than fabricate one.
        return
    resource = str(payload["resource"])
    operation = str(payload["operation"])
    envelope = build_operation_envelope(
        operation_id=operation_id,
        request_id=request_id,
        resource=resource,
        operation=operation,
        payload=operation_input_payload(target, resource, request),
    )
    OperationInput.objects.create(
        operation_id=operation_id,
        request_id=request_id,
        resource=resource,
        operation=operation,
        contract_version=envelope["contract_version"],
        envelope=envelope,
    )


def enqueue_provisioner_launch(command: list[str]) -> str:
    """Persist one durable intent per authorized operation and return its UUID."""
    payload = validate_provisioner_command(command)
    with transaction.atomic():
        operation_id = _operation_identity(payload)
        existing = ProvisionerLaunchIntent.objects.filter(operation_id=operation_id).first()
        if existing is not None:
            return str(existing.intent_id)
        canonical = f"{'|'.join(command)}|{operation_id}"
        idempotency_key = sha256(canonical.encode("utf-8")).hexdigest()
        existing = ProvisionerLaunchIntent.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return str(existing.intent_id)
        intent_id = uuid4()
        namespace = str(getattr(settings, "ENGINE_TASK_CLUSTER", "") or "")
        task_ref = (
            f"{namespace}/{build_idempotent_job_name(PROVISIONER_CONTAINER_NAME, str(intent_id))}" if namespace else ""
        )
        row = ProvisionerLaunchIntent.objects.create(
            intent_id=intent_id,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload=payload,
            task_ref=task_ref,
            next_attempt_at=timezone.now(),
        )
        _materialize_operation_input(payload, operation_id)
        return str(row.intent_id)


def task_ref_for_intent(intent_id: str) -> str:
    """Return the provider task reference reserved for a queued intent."""
    return ProvisionerLaunchIntent.objects.only("task_ref").get(intent_id=UUID(intent_id)).task_ref
