"""Sanitized ACES identity-domain topology admission (#1606).

ACES owns topology validation. Shifter preserves the public diagnostic identity
while replacing value-bearing messages before they can enter operational output.
"""

from __future__ import annotations

from aces_backend_protocols.capabilities import ProvisionerCapabilities
from aces_backend_protocols.domain_topology import domain_topology_plan_diagnostics
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import ProvisioningPlan
from aces_contracts.runtime_state import RuntimeSnapshot

_MESSAGE = "authored identity-domain topology is invalid or unsupported by this backend"


def sanitized_domain_topology_diagnostics(
    plan: ProvisioningPlan,
    capabilities: ProvisionerCapabilities,
    snapshot: RuntimeSnapshot | None = None,
) -> list[Diagnostic]:
    """Return public topology diagnostics with value-free Shifter messages."""
    return [
        Diagnostic(
            code=diagnostic.code,
            domain="provisioning",
            address=diagnostic.address or "plan",
            message=_MESSAGE,
            severity=Severity.ERROR,
        )
        for diagnostic in domain_topology_plan_diagnostics(
            plan,
            snapshot=snapshot,
            supported_domain_profiles=capabilities.supported_domain_profiles,
        )
    ]
