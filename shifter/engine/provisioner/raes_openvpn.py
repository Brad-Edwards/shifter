"""Participant OpenVPN access on the RAES GCE range-cell path (ADR-039-R10, #2030).

Composes the provider-neutral credential lifecycle (``vpn_access``) with the
range's remote-access authority from the immutable operation input (ADR-043):
the capability and the reserved gateway pool slot never come from
``mission_control_range``.

Credentials are keyed on the range's ``request_id`` -- the generation the Engine
validates profile delivery against -- for a cold provision and a warm activation
alike. The server identity is stored before the apply so the gateway can read it
at boot; health verification and profile publication run inside the apply, so a
gateway that never becomes ready is cleaned up with the rest of the attempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.raes.operation_input import RaesRemoteAccess

from raes_gcp_network_allocation import RaesRealizationError
from raes_gcp_plan import RaesGceRemoteAccess
from raes_plan import RaesPlan
from vpn_access import (
    OpenVpnPreparation,
    VpnSecretOps,
    cleanup_openvpn_access,
    prepare_openvpn_access,
    publish_openvpn_profile,
    verify_openvpn_gateway,
)
from vpn_secrets import GCPVpnSecretOps, openvpn_access_enabled

__all__ = ["RaesOpenVpnSession", "cleanup_raes_openvpn", "names_only_remote_access", "prepare_raes_openvpn"]


@dataclass(frozen=True)
class RaesOpenVpnSession:
    """One generation's prepared OpenVPN credentials for a GCE apply."""

    remote_access: RaesRemoteAccess
    preparation: OpenVpnPreparation
    secret_ops: VpnSecretOps

    def plan_remote_access(self) -> RaesGceRemoteAccess:
        """Return the gateway planning inputs, including the exact identity secret."""
        return RaesGceRemoteAccess(
            target_ref=self.remote_access.target_ref,
            gateway_pool_slot=self.remote_access.gateway_pool_slot,
            server_secret_ref=self.preparation.server_secret_ref,
        )

    def publish(self, gateway: object) -> dict[str, object]:
        """Verify the gateway's service-and-policy health, then publish the profile."""
        return publish_openvpn_profile(self.preparation, verify_openvpn_gateway(gateway), self.secret_ops)


def names_only_remote_access(remote_access: RaesRemoteAccess | None) -> RaesGceRemoteAccess | None:
    """Return gateway planning inputs for destroy/inventory, which need only names."""
    if remote_access is None:
        return None
    return RaesGceRemoteAccess(target_ref=remote_access.target_ref, gateway_pool_slot=remote_access.gateway_pool_slot)


def _member_refs(raes_plan: RaesPlan) -> list[str]:
    """Return every realized member key the plan declares (``<node address>#<index>``)."""
    return [f"{node.address}#{index}" for node in raes_plan.nodes for index in range(node.count)]


def _secret_ops(remote_access: RaesRemoteAccess) -> VpnSecretOps:
    """Return GCP secret ops bound to the projected gateway pool slot."""
    return GCPVpnSecretOps(gateway_pool_slot=remote_access.gateway_pool_slot)


def prepare_raes_openvpn(
    request_id: str,
    range_id: int,
    raes_plan: RaesPlan,
    remote_access: RaesRemoteAccess | None,
) -> RaesOpenVpnSession | None:
    """Mint/reuse the generation's credentials before any gateway exists.

    Fails closed when this installation cannot realize the authorized gateway or
    the credential window is no longer valid, before any provider mutation.
    """
    if remote_access is None:
        return None
    if not openvpn_access_enabled():
        raise RaesRealizationError(
            "This range requests OpenVPN access, but the GCE adapter is not configured to realize it"
        )
    secret_ops = _secret_ops(remote_access)
    preparation = prepare_openvpn_access(
        request_id,
        range_id,
        _member_refs(raes_plan),
        remote_access.capability,
        secret_ops,
    )
    return RaesOpenVpnSession(remote_access=remote_access, preparation=preparation, secret_ops=secret_ops)


def cleanup_raes_openvpn(request_id: str, range_id: int, remote_access: RaesRemoteAccess | None) -> None:
    """Delete every credential of the range's OpenVPN generation (idempotent)."""
    if remote_access is None:
        return
    cleanup_openvpn_access(range_id, request_id, _secret_ops(remote_access))
