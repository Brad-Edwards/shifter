"""Capability-envelope validation for ACES composition placements (ADR-032).

Split out of ``runtime_target`` (Sonar file-size): fail-closed diagnostics for
``content-placement`` / ``feature-binding`` / ``account-placement`` resources --
content type against ``supported_content_types``, account features (via the shared
``provisioner_account_features``) against ``supported_account_features``, and every
placement's target node present in the plan.

Account features are gated against two independent envelopes (#1563): the manifest
declaration (``supported_account_features``, plan-time vocabulary) and the
hand-authored realization evidence
(``shared.aces.realization_ledger.REALIZED_ACCOUNT_FEATURES``). A requested feature
outside the declaration is an ``unsupported-account-feature``; a feature that is
declared but not evidence-backed is an ``account-feature-not-realized`` -- so the
manifest cannot silently over-claim account realization.
"""

from __future__ import annotations

from collections.abc import Mapping

from aces_backend_protocols.account_features import provisioner_account_features
from aces_backend_protocols.capabilities import ProvisionerCapabilities
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import ChangeAction, PlannedResource, PlanOperation

from shared.aces.realization_ledger import REALIZED_ACCOUNT_FEATURES
from shared.log_sanitize import safe_log_value

_DOMAIN = "provisioning"
CONTENT_PLACEMENT_RESOURCE_TYPE = "content-placement"
FEATURE_BINDING_RESOURCE_TYPE = "feature-binding"
ACCOUNT_PLACEMENT_RESOURCE_TYPE = "account-placement"
#: Composition placement resource types (content/features/accounts), all PROVISIONING.
COMPOSITION_RESOURCE_TYPES: frozenset[str] = frozenset(
    {CONTENT_PLACEMENT_RESOURCE_TYPE, FEATURE_BINDING_RESOURCE_TYPE, ACCOUNT_PLACEMENT_RESOURCE_TYPE}
)


def _diagnostic(code: str, address: str, message: str) -> Diagnostic:
    """Build an ERROR provisioning diagnostic."""
    return Diagnostic(code=code, domain=_DOMAIN, address=address, message=message, severity=Severity.ERROR)


def _placement_target(payload: Mapping[str, object]) -> str:
    """Return the resolved target node address of a composition placement, or ''."""
    for key in ("target_address", "node_address"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unbound_placement_diagnostics(
    resource: PlannedResource, target: str, node_addresses: set[str]
) -> list[Diagnostic]:
    """Fail closed when a placement's target node is not present in the plan."""
    if target and target in node_addresses:
        return []
    return [
        _diagnostic(
            "shifter-provisioner.unbound-placement",
            resource.address,
            f"placement targets node '{safe_log_value(target)}' not present in this plan",
        )
    ]


def _content_placement_diagnostics(
    resource: PlannedResource,
    payload: Mapping[str, object],
    capabilities: ProvisionerCapabilities,
    node_addresses: set[str],
) -> list[Diagnostic]:
    """Return capability-envelope diagnostics for a content-placement resource."""
    diagnostics: list[Diagnostic] = []
    spec = payload.get("spec")
    content_type = spec.get("type") if isinstance(spec, Mapping) else None
    if isinstance(content_type, str) and content_type.lower() not in capabilities.supported_content_types:
        diagnostics.append(
            _diagnostic(
                "shifter-provisioner.unsupported-content-type",
                resource.address,
                f"unsupported content type '{safe_log_value(content_type)}' "
                f"(supported: {sorted(capabilities.supported_content_types)})",
            )
        )
    diagnostics.extend(_unbound_placement_diagnostics(resource, _placement_target(payload), node_addresses))
    return diagnostics


def _account_feature_diagnostics(
    address: str, spec: Mapping[str, object], capabilities: ProvisionerCapabilities
) -> list[Diagnostic]:
    """Fail-closed account-feature diagnostics for one account spec.

    Two independent envelopes (#1563): the manifest declaration
    (``supported_account_features``) and the hand-authored realization evidence
    (``REALIZED_ACCOUNT_FEATURES``). A requested feature outside the declaration is
    ``unsupported-account-feature``; a declared-but-not-evidence-backed feature is
    ``account-feature-not-realized``. The two branches are mutually exclusive so a
    single feature never double-reports.
    """
    diagnostics: list[Diagnostic] = []
    for feature in sorted(provisioner_account_features(spec)):
        if feature not in capabilities.supported_account_features:
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.unsupported-account-feature",
                    address,
                    f"unsupported account feature '{feature}' "
                    f"(supported: {sorted(capabilities.supported_account_features)})",
                )
            )
        elif feature not in REALIZED_ACCOUNT_FEATURES:
            # Independent evidence gate (#1563): the manifest declares this feature
            # but the backend does not genuinely realize it. Fail closed rather than
            # trust the declaration, so a manifest over-claim cannot reach dispatch.
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.account-feature-not-realized",
                    address,
                    f"account feature '{feature}' is declared but not evidence-backed; "
                    "this backend does not realize it",
                )
            )
    return diagnostics


def _account_placement_diagnostics(
    resource: PlannedResource,
    payload: Mapping[str, object],
    capabilities: ProvisionerCapabilities,
    node_addresses: set[str],
) -> list[Diagnostic]:
    """Return capability-envelope diagnostics for an account-placement resource."""
    diagnostics: list[Diagnostic] = []
    spec = payload.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    if not capabilities.supports_accounts:
        diagnostics.append(
            _diagnostic(
                "shifter-provisioner.accounts-unsupported",
                resource.address,
                "this backend does not realize account placements",
            )
        )
    else:
        diagnostics.extend(_account_feature_diagnostics(resource.address, spec, capabilities))
    diagnostics.extend(_unbound_placement_diagnostics(resource, _placement_target(payload), node_addresses))
    return diagnostics


def account_operation_diagnostics(
    operations: list[PlanOperation], capabilities: ProvisionerCapabilities
) -> list[Diagnostic]:
    """Account-feature diagnostics for materializing account-placement operations (#1563).

    The resource pass gates the plan's resource view; this covers the case where an
    account payload is carried only by a materializing (CREATE / UPDATE) operation, or
    diverges from the resource snapshot, so an over-claimed feature cannot slip past
    the realization ledger via an operation-only payload before the plan is dispatched.
    A DELETE operation removes an account and does not materialize its historical
    features, so it is exempt; UNCHANGED operations are no-ops. The caller
    deduplicates these against the resource-pass diagnostics.
    """
    diagnostics: list[Diagnostic] = []
    for operation in operations:
        if operation.resource_type != ACCOUNT_PLACEMENT_RESOURCE_TYPE:
            continue
        if operation.action not in (ChangeAction.CREATE, ChangeAction.UPDATE):
            continue
        if not capabilities.supports_accounts:
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.accounts-unsupported",
                    operation.address,
                    "this backend does not realize account placements",
                )
            )
            continue
        payload = operation.payload
        spec = payload.get("spec") if isinstance(payload, Mapping) else None
        spec = spec if isinstance(spec, Mapping) else {}
        diagnostics.extend(_account_feature_diagnostics(operation.address, spec, capabilities))
    return diagnostics


def composition_diagnostics(
    resource: PlannedResource,
    payload: Mapping[str, object],
    capabilities: ProvisionerCapabilities,
    node_addresses: set[str],
) -> list[Diagnostic]:
    """Dispatch envelope validation for one composition placement resource."""
    if resource.resource_type == CONTENT_PLACEMENT_RESOURCE_TYPE:
        return _content_placement_diagnostics(resource, payload, capabilities, node_addresses)
    if resource.resource_type == ACCOUNT_PLACEMENT_RESOURCE_TYPE:
        return _account_placement_diagnostics(resource, payload, capabilities, node_addresses)
    # feature-binding: no per-feature capability gate; only the target must resolve.
    return _unbound_placement_diagnostics(resource, _placement_target(payload), node_addresses)
