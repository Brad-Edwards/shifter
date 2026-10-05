"""Realize the participant OpenVPN gateway within an RAES GCE apply (ADR-039-R10, #2030).

Split out of ``raes_gcp_apply`` (Sonar S104). The gateway is created right after
the hosts exist, so it boots while the range is verified, and is probed and its
profile published afterwards. Both run inside the apply's cleanup scope, so a
gateway that never becomes healthy is removed with the rest of the attempt.
"""

from __future__ import annotations

from typing import Any

from gcp_range_cell_types import RangeCellPlan, ResourceDict
from gcp_range_cells import _ensure_openvpn_gateway
from raes_gcp_apply_types import RaesGceApplyOptions, RaesGceApplyRuntime
from raes_gcp_vpn_plan import RaesGceRemoteAccess

__all__ = ["ensure_vpn_gateway", "publish_vpn_access", "remote_access_plan"]


def remote_access_plan(options: RaesGceApplyOptions) -> RaesGceRemoteAccess | None:
    """Return the gateway planning inputs for a realizing apply, if any."""
    return options.openvpn.plan_remote_access() if options.openvpn is not None else None


def ensure_vpn_gateway(plan: RangeCellPlan, runtime: RaesGceApplyRuntime) -> ResourceDict | None:
    """Create the gateway when this generation realizes one; return its readiness facts."""
    if runtime.openvpn is None:
        return None
    return _ensure_openvpn_gateway(plan, runtime.clients, runtime.config)


def publish_vpn_access(runtime: RaesGceApplyRuntime, gateway: ResourceDict | None) -> dict[str, Any]:
    """Verify the gateway and publish the profile; return the apply-result fragment."""
    if runtime.openvpn is None:
        return {}
    return {"vpn_access": runtime.openvpn.publish(gateway)}
