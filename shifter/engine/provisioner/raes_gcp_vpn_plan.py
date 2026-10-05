"""Plan the participant OpenVPN gateway of an RAES GCE range cell (ADR-039-R10, #2030).

Split out of ``raes_gcp_plan`` (Sonar S104). The gateway is planned beside the
capability's target member and forwards only that member's declared participant
channels, so the tunnel reaches exactly what portal access reaches.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import GCERangeCellConfig
from gcp_range_cell_firewall import PARTICIPANT_CHANNEL_PORTS
from gcp_range_cell_plan import OpenVpnGatewayRequest, _openvpn_gateway_plan
from gcp_range_cell_types import InstancePlan, OpenVpnGatewayPlan, SubnetPlan
from raes_gcp_plan_errors import RaesGcePlanError

__all__ = ["RaesGceRemoteAccess", "vpn_gateway_plan"]


@dataclass(frozen=True)
class RaesGceRemoteAccess:
    """The range's OpenVPN gateway planning inputs.

    ``server_secret_ref`` is the exact identity secret the gateway reads. It is
    present only when the plan realizes the gateway; destroy and inventory plan
    resource names only and leave it empty.
    """

    target_ref: str
    gateway_pool_slot: int
    server_secret_ref: str = ""

    def names_only(self) -> RaesGceRemoteAccess:
        """Return the inputs destroy and inventory need: resource names, no identity secret."""
        return RaesGceRemoteAccess(target_ref=self.target_ref, gateway_pool_slot=self.gateway_pool_slot)


def vpn_gateway_plan(
    range_id: int,
    instance_plans: list[InstancePlan],
    subnet_plans: list[SubnetPlan],
    config: GCERangeCellConfig,
    remote_access: RaesGceRemoteAccess | None,
) -> OpenVpnGatewayPlan | None:
    """Plan the OpenVPN gateway beside the authorized member, or ``None`` without one."""
    if remote_access is None:
        return None
    realizing = bool(remote_access.server_secret_ref)
    target = next((instance for instance in instance_plans if instance["uuid"] == remote_access.target_ref), None)
    channels = target.get("participant_access_channels", []) if target is not None else []
    ports = tuple(sorted({PARTICIPANT_CHANNEL_PORTS[channel] for channel in channels}, key=int))
    if realizing and not ports:
        raise RaesGcePlanError("the OpenVPN target declares no participant channel to forward")
    request = OpenVpnGatewayRequest(
        target_ref=remote_access.target_ref,
        pool_slot=remote_access.gateway_pool_slot,
        target_ports=ports,
        server_secret_ref=remote_access.server_secret_ref,
    )
    try:
        return _openvpn_gateway_plan(
            range_id, instance_plans, subnet_plans, config, request, require_provision_values=realizing
        )
    except RuntimeError as exc:
        raise RaesGcePlanError(str(exc)) from None
