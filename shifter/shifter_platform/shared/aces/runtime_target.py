"""Shifter's ACES ``RuntimeTarget`` provisioning adapter (issue #1262).

This module is a **translation boundary**, not a second launch path. It:

1. validates an ACES ``ProvisioningPlan`` against Shifter's ``provisioning-only``
   capability envelope (:mod:`shared.aces.manifest`'s
   ``SHIFTER_PROVISIONER_CAPABILITIES`` is the source of truth for what is
   supported);
2. translates a *supported* plan into :class:`ShifterProvisioningIntent`, a
   Shifter-shaped range-creation input; and
3. drives an injected :class:`ShifterRangeRealizationPort` to realize that
   intent.

It deliberately does **not** dispatch a live range itself. There is no
``create_range``/ECS/provisioner call anywhere in this module -- that stays
behind the injected port, whose only concrete implementation
(:class:`cms.aces.range_realization.CmsRangeRealizationPort`) produces a valid
wrapped Shifter spec through the incumbent hydration path and returns IDs and
status only. Flipping catalog launchability and live dispatch are out of scope
for this slice (see issues #1263 / #1264).

Per ADR-024, ``shared`` must never import ``cms`` or ``engine``: the concrete
realization implementation lives in ``cms.aces.range_realization`` and is
handed in here as a :class:`typing.Protocol`-typed port. This is the second
(and, for the ``provisioning-only`` profile, last) module allowed to import
the ``aces-sdl`` SDL packages -- see :mod:`shared.aces.manifest` and the
invariant recorded in :mod:`shared.aces.status`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aces_backend_protocols.capabilities import BackendManifest
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import PlannedResource, ProvisioningPlan, RuntimeDomain
from aces_contracts.runtime_state import ApplyResult, RuntimeSnapshot, SnapshotEntry
from aces_runtime.registry import BackendRegistry, RuntimeTarget, RuntimeTargetComponents

from shared.aces.contracts import SHIFTER_BACKEND_NAME
from shared.aces.manifest import create_shifter_backend_manifest
from shared.log_sanitize import safe_log_value

__all__ = [
    "NETWORK_RESOURCE_TYPE",
    "NODE_RESOURCE_TYPE",
    "SUPPORTED_OS_FAMILIES",
    "SUPPORTED_RESOURCE_TYPES",
    "ShifterProvisioner",
    "ShifterProvisioningIntent",
    "ShifterRangeRealizationPort",
    "ShifterRealizationResult",
    "create_shifter_backend_components",
    "create_shifter_backend_target",
    "register_shifter_backend",
]

_DOMAIN = "provisioning"

#: The only two provisioning resource types Shifter's incumbent range-creation
#: path can realize: a compute node (VM) and a network. Anything else is a
#: capability claim Shifter cannot honor (ADR-024 fail-closed boundary).
NODE_RESOURCE_TYPE = "node"
NETWORK_RESOURCE_TYPE = "network"
SUPPORTED_RESOURCE_TYPES: frozenset[str] = frozenset({NODE_RESOURCE_TYPE, NETWORK_RESOURCE_TYPE})

#: Mirrors ``shared.aces.manifest.SHIFTER_PROVISIONER_CAPABILITIES.supported_os_families``.
#: Kept as a plain module constant (rather than importing the capability object
#: at validation time) so the hot validation path never touches the ACES SDL
#: capability dataclass machinery.
SUPPORTED_OS_FAMILIES: frozenset[str] = frozenset({"linux", "windows"})

#: Resource types that claim account/ACL/content-placement realization.
#: ``SHIFTER_PROVISIONER_CAPABILITIES`` declares ``supports_acls=False`` and
#: ``supports_accounts=False``, so these are always rejected.
_PLACEMENT_ACCOUNT_ACL_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"account", "acl", "placement", "content-placement", "account-placement", "feature-binding"}
)

#: Resource types that ask the backend to execute a runtime command. Shifter's
#: provisioning-only profile has no orchestrator, so there is nothing to
#: execute a command through.
_RUNTIME_COMMAND_RESOURCE_TYPES: frozenset[str] = frozenset({"runtime-command", "command", "exec"})

#: Resource types that ask for raw runtime snapshot/history access. Shifter's
#: ``Provisioner`` never exposes ``RuntimeSnapshot`` internals beyond the
#: bounded IDs/status entries this module writes itself.
_SNAPSHOT_HISTORY_RESOURCE_TYPES: frozenset[str] = frozenset({"snapshot-request", "history-request", "state-query"})

#: Payload keys a supported ``node``/``network`` resource may carry. Anything
#: else is an author-supplied provider realization detail (Terraform
#: variables, image ids, CIDRs, SSM/SSH material, ...) that Shifter's backend
#: -- not the ACES scenario author -- owns.
_NODE_ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset({"os_family", "name", "scenario_ref"})
_NETWORK_ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset({"name", "scenario_ref"})


@dataclass(frozen=True)
class ShifterProvisioningIntent:
    """Translation output: a supported plan reduced to range-creation inputs.

    Carries only what :class:`ShifterRangeRealizationPort` implementations
    need to hydrate a Shifter range -- never raw plan payloads, provider
    details, or secrets.
    """

    scenario_ref: str
    node_counts_by_os: Mapping[str, int]
    network_addresses: tuple[str, ...]


@dataclass(frozen=True)
class ShifterRealizationResult:
    """Realization outcome: IDs and status only -- never a raw spec or secret."""

    range_uuid: str
    status: str


class ShifterRangeRealizationPort(Protocol):
    """Injected seam so this module never imports ``cms`` or ``engine`` (ADR-024).

    The concrete implementation (``cms.aces.range_realization.CmsRangeRealizationPort``)
    is constructed with the CMS launch context (a Django user + an
    ``agents_by_os`` mapping) and reuses the incumbent hydration path.
    """

    def realize(self, intent: ShifterProvisioningIntent) -> ShifterRealizationResult:
        """Realize ``intent`` and return its IDs/status only."""
        ...


def _diagnostic(code: str, address: str, message: str) -> Diagnostic:
    """Build an ERROR-severity provisioning diagnostic for ``address``."""
    return Diagnostic(code=code, domain=_DOMAIN, address=address, message=message, severity=Severity.ERROR)


def _resolve_scenario_ref(scenario_refs_by_address: dict[str, str]) -> tuple[str | None, list[Diagnostic]]:
    """Reduce the per-resource ``scenario_ref`` claims to a single plan-wide value."""
    distinct_values = sorted(set(scenario_refs_by_address.values()))
    if not distinct_values:
        return None, [
            _diagnostic(
                "shifter-provisioner.missing-scenario-ref",
                "plan",
                "Supported plans must declare a 'scenario_ref' payload key on at least "
                "one provisioning resource so Shifter knows which scenario template to hydrate.",
            )
        ]
    if len(distinct_values) > 1:
        return None, [
            _diagnostic(
                "shifter-provisioner.conflicting-scenario-ref",
                "plan",
                f"Plan resources declared conflicting 'scenario_ref' values: {distinct_values}.",
            )
        ]
    return distinct_values[0], []


@dataclass(frozen=True)
class _ResourceOutcome:
    """Per-resource classification: either an error diagnostic or a contribution."""

    diagnostic: Diagnostic | None = None
    os_family: str | None = None
    network_address: str | None = None
    scenario_ref: str | None = None


#: Categorically unsupported provisioning resource kinds, as
#: (resource-type set, diagnostic-code suffix, human phrase). Data-driven so the
#: rejection check stays a simple loop rather than one branch per kind.
_UNSUPPORTED_RESOURCE_KINDS: tuple[tuple[frozenset[str], str, str], ...] = (
    (_PLACEMENT_ACCOUNT_ACL_RESOURCE_TYPES, "placement-unsupported", "placement/account/ACL resource type"),
    (_RUNTIME_COMMAND_RESOURCE_TYPES, "runtime-command-unsupported", "runtime command execution resource type"),
    (_SNAPSHOT_HISTORY_RESOURCE_TYPES, "snapshot-history-unsupported", "raw snapshot/history request resource type"),
)


def _rejection_diagnostic(resource: PlannedResource) -> Diagnostic | None:
    """Return an error diagnostic if ``resource`` is a categorically unsupported kind.

    Covers non-provisioning domains, placement/account/ACL, runtime-command, raw
    snapshot/history, and unknown resource types. Returns ``None`` when the type
    is one Shifter's provisioning-only envelope can realize.
    """
    if resource.domain != RuntimeDomain.PROVISIONING:
        return _diagnostic(
            "shifter-provisioner.unsupported-domain",
            resource.address,
            f"Shifter's provisioning-only backend does not support domain "
            f"'{resource.domain.value}' for '{resource.address}'.",
        )
    for resource_types, code_suffix, phrase in _UNSUPPORTED_RESOURCE_KINDS:
        if resource.resource_type in resource_types:
            return _diagnostic(
                f"shifter-provisioner.{code_suffix}",
                resource.address,
                f"Shifter's provisioning-only backend does not support {phrase} "
                f"'{resource.resource_type}' for '{resource.address}'.",
            )
    if resource.resource_type not in SUPPORTED_RESOURCE_TYPES:
        return _diagnostic(
            "shifter-provisioner.unsupported-resource-type",
            resource.address,
            f"Shifter's provisioning-only backend does not support resource type "
            f"'{resource.resource_type}' for '{resource.address}' "
            f"(supported: {sorted(SUPPORTED_RESOURCE_TYPES)}).",
        )
    return None


def _payload_diagnostic(resource: PlannedResource, *, is_node: bool) -> Diagnostic | None:
    """Return an error diagnostic if ``resource``'s payload is malformed or over-specified.

    Rejects a non-mapping payload and any author-supplied key outside the
    node/network allow-list (provider realization detail Shifter's backend, not
    the ACES scenario author, owns). Returns ``None`` for an acceptable payload.
    """
    payload = resource.payload
    if not isinstance(payload, Mapping):
        return _diagnostic(
            "shifter-provisioner.invalid-payload",
            resource.address,
            f"Expected a mapping payload for '{resource.address}'.",
        )
    allowed_keys = _NODE_ALLOWED_PAYLOAD_KEYS if is_node else _NETWORK_ALLOWED_PAYLOAD_KEYS
    unexpected_keys = sorted(set(payload) - allowed_keys)
    if unexpected_keys:
        return _diagnostic(
            "shifter-provisioner.provider-detail-not-allowed",
            resource.address,
            "Shifter's provisioning-only backend does not accept author-supplied "
            f"provider realization detail keys {unexpected_keys} for '{resource.address}'; "
            "the backend -- not the ACES scenario author -- owns realization detail.",
        )
    return None


def _classify_resource(resource: PlannedResource) -> _ResourceOutcome:
    """Validate one provisioning resource and return its diagnostic or contribution."""
    rejection = _rejection_diagnostic(resource)
    if rejection is not None:
        return _ResourceOutcome(diagnostic=rejection)

    is_node = resource.resource_type == NODE_RESOURCE_TYPE
    payload_error = _payload_diagnostic(resource, is_node=is_node)
    if payload_error is not None:
        return _ResourceOutcome(diagnostic=payload_error)

    payload = resource.payload
    raw_scenario_ref = payload.get("scenario_ref")
    scenario_ref = raw_scenario_ref if isinstance(raw_scenario_ref, str) and raw_scenario_ref else None

    if not is_node:
        return _ResourceOutcome(network_address=resource.address, scenario_ref=scenario_ref)

    os_family = payload.get("os_family")
    if os_family not in SUPPORTED_OS_FAMILIES:
        return _ResourceOutcome(
            diagnostic=_diagnostic(
                "shifter-provisioner.unsupported-os-family",
                resource.address,
                f"Shifter's provisioning capability envelope does not support os_family "
                f"'{safe_log_value(os_family)}' for '{resource.address}' "
                f"(supported: {sorted(SUPPORTED_OS_FAMILIES)}).",
            )
        )
    return _ResourceOutcome(os_family=os_family, scenario_ref=scenario_ref)


def _validate_and_translate(
    plan: ProvisioningPlan,
) -> tuple[ShifterProvisioningIntent | None, list[Diagnostic]]:
    """Validate ``plan`` against Shifter's capability envelope and translate it.

    Returns ``(None, diagnostics)`` with at least one error diagnostic when the
    plan claims anything Shifter's ``provisioning-only`` profile cannot honor.
    Returns ``(intent, diagnostics)`` for a fully supported plan.
    """
    diagnostics: list[Diagnostic] = []
    node_counts: dict[str, int] = {}
    network_addresses: list[str] = []
    scenario_refs_by_address: dict[str, str] = {}

    for resource in plan.resources.values():
        outcome = _classify_resource(resource)
        if outcome.diagnostic is not None:
            diagnostics.append(outcome.diagnostic)
            continue
        if outcome.scenario_ref is not None:
            scenario_refs_by_address[resource.address] = outcome.scenario_ref
        if outcome.os_family is not None:
            node_counts[outcome.os_family] = node_counts.get(outcome.os_family, 0) + 1
        elif outcome.network_address is not None:
            network_addresses.append(outcome.network_address)

    scenario_ref_value, scenario_diagnostics = _resolve_scenario_ref(scenario_refs_by_address)
    diagnostics.extend(scenario_diagnostics)

    if scenario_ref_value is None or any(diagnostic.is_error for diagnostic in diagnostics):
        return None, diagnostics

    intent = ShifterProvisioningIntent(
        scenario_ref=scenario_ref_value,
        node_counts_by_os=dict(node_counts),
        network_addresses=tuple(sorted(network_addresses)),
    )
    return intent, diagnostics


class ShifterProvisioner:
    """Provisioner implementation for Shifter's ``provisioning-only`` ACES backend.

    Implements the ``aces_backend_protocols.protocols.Provisioner`` structural
    protocol. ``validate`` is pure (no port call); ``apply`` re-validates,
    refuses to call the port on any error diagnostic, and otherwise drives the
    injected port and records only IDs/status in the returned snapshot.
    """

    def __init__(self, port: ShifterRangeRealizationPort) -> None:
        self._port = port

    @staticmethod
    def validate(plan: ProvisioningPlan) -> list[Diagnostic]:
        """Return capability-envelope diagnostics for ``plan`` without realizing it."""
        _, diagnostics = _validate_and_translate(plan)
        return diagnostics

    def apply(self, plan: ProvisioningPlan, snapshot: RuntimeSnapshot) -> ApplyResult:
        """Validate, translate, and realize ``plan`` through the injected port.

        Never calls the port when validation produced an error diagnostic. A
        port failure (any raised exception) is caught at this boundary and
        surfaced as an ``ApplyResult`` diagnostic -- it never propagates as a
        raw exception past this adapter.
        """
        intent, diagnostics = _validate_and_translate(plan)
        if intent is None:
            return ApplyResult(success=False, snapshot=snapshot, diagnostics=diagnostics)

        try:
            result = self._port.realize(intent)
        except Exception as exc:
            failure = _diagnostic(
                "shifter-provisioner.realization-failed",
                "plan",
                f"Range realization failed: {safe_log_value(exc)}",
            )
            return ApplyResult(success=False, snapshot=snapshot, diagnostics=[*diagnostics, failure])

        entries = dict(snapshot.entries)
        changed_addresses: list[str] = []
        for operation in plan.actionable_operations:
            if operation.resource_type not in SUPPORTED_RESOURCE_TYPES:
                continue
            entries[operation.address] = SnapshotEntry(
                address=operation.address,
                domain=RuntimeDomain.PROVISIONING,
                resource_type=operation.resource_type,
                payload={"range_uuid": result.range_uuid, "status": result.status},
                status=result.status,
            )
            changed_addresses.append(operation.address)

        return ApplyResult(
            success=True,
            snapshot=snapshot.with_entries(entries),
            diagnostics=diagnostics,
            changed_addresses=changed_addresses,
        )


def create_shifter_backend_components(
    *,
    manifest: BackendManifest,
    port: ShifterRangeRealizationPort,
    **config: Any,
) -> RuntimeTargetComponents:
    """Build the ``provisioning-only`` Shifter backend components for ``manifest``.

    Only a provisioner is returned: ``manifest`` declares no orchestrator,
    evaluator, or participant-runtime capability, so component presence
    matches the manifest shape the registry validates against.
    """
    del manifest, config
    return RuntimeTargetComponents(provisioner=ShifterProvisioner(port=port))


def register_shifter_backend(registry: BackendRegistry) -> None:
    """Register the Shifter backend descriptor on ``registry``."""
    registry.register(SHIFTER_BACKEND_NAME, create_shifter_backend_manifest, create_shifter_backend_components)


def create_shifter_backend_target(*, port: ShifterRangeRealizationPort, **config: Any) -> RuntimeTarget:
    """Return a fully configured, provisioning-only Shifter ``RuntimeTarget``."""
    manifest = create_shifter_backend_manifest(**config)
    components = create_shifter_backend_components(manifest=manifest, port=port, **config)
    return RuntimeTarget(
        name=SHIFTER_BACKEND_NAME,
        manifest=manifest,
        provisioner=components.provisioner,
    )
