"""Capability-envelope validation for ACES composition placements (ADR-032).

Split out of ``runtime_target`` (Sonar file-size): fail-closed diagnostics for
``content-placement`` / ``feature-binding`` / ``account-placement`` resources --
content type against ``supported_content_types``, account features (via the shared
``provisioner_account_features``) against ``supported_account_features``, and every
placement's target node present in the plan.
"""

from __future__ import annotations

from collections.abc import Mapping

from aces_backend_protocols.account_features import provisioner_account_features
from aces_backend_protocols.capabilities import ProvisionerCapabilities
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import PlannedResource

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
        for feature in sorted(provisioner_account_features(spec)):
            if feature not in capabilities.supported_account_features:
                diagnostics.append(
                    _diagnostic(
                        "shifter-provisioner.unsupported-account-feature",
                        resource.address,
                        f"unsupported account feature '{feature}' "
                        f"(supported: {sorted(capabilities.supported_account_features)})",
                    )
                )
    diagnostics.extend(_unbound_placement_diagnostics(resource, _placement_target(payload), node_addresses))
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
