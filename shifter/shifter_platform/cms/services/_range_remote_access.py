"""Create-time OpenVPN capability minting for a range launch.

Extracted from ``_range_create`` (Sonar S104). Decides whether a launch mints
OpenVPN authority and against which scenario target; the backend-capability half
of the decision lives beside the range-backend admission gate in
``_range_backend_admission._openvpn_backend_admitted``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cms.exceptions import CMSError

if TYPE_CHECKING:
    from datetime import datetime

    from shared.schemas.range import RangeSpec


def _build_remote_access_capability(
    range_spec: RangeSpec,
    teardown_at: datetime | None,
    *,
    backend_admitted: bool = True,
    required: bool,
) -> dict[str, object] | None:
    """Mint OpenVPN authority when the product and scenario support it.

    ``RANGE_OPENVPN_ENABLED`` is the deployment's answer to "do we want OpenVPN
    at all", distinct from ``backend_admitted``, which answers "can this backend
    do OpenVPN". Without the deployment switch, every CTF range on a capable
    backend whose scenario exposes a single participant-visible Kali attacker
    provisions a dedicated gateway VM, an external address, and per-range VPN
    secrets, and runs an extra guest-setup step that can fail -- real cost and a
    real failure mode for an access path an event may never use. An event that
    reaches ranges only through the portal turns this off.

    A caller that explicitly requires OpenVPN still fails loudly rather than
    silently launching without it.
    """
    from django.conf import settings

    capability = None
    if not getattr(settings, "RANGE_OPENVPN_ENABLED", True):
        if required:
            raise CMSError("OpenVPN access is disabled for this deployment")
        return None
    if teardown_at is not None and not backend_admitted:
        if required:
            raise CMSError("OpenVPN access is unavailable on the selected range backend")
    elif teardown_at is not None:
        target_uuid = _remote_access_target_uuid(range_spec)
        if target_uuid is None:
            if required:
                raise CMSError("OpenVPN access requires exactly one identified Kali attacker target")
        else:
            from shared.remote_access import build_openvpn_capability

            capability = build_openvpn_capability(target_uuid, teardown_at)
    return capability


def _remote_access_target_uuid(range_spec: RangeSpec) -> str | None:
    """Return the sole participant-visible Kali attacker UUID, when unambiguous."""
    participant_targets = {binding.target_ref for binding in range_spec.participant_access}
    kali_targets = [
        instance for instance in range_spec.all_instances if instance.role == "attacker" and instance.os_type == "kali"
    ]
    targets = (
        [instance for instance in kali_targets if str(instance.uuid) in participant_targets]
        if participant_targets
        else kali_targets
    )
    if len(targets) != 1:
        return None
    return targets[0].uuid
