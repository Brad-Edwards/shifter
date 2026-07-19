"""CMS authorization boundary for per-range OpenVPN profiles.

The CTF entry points gate on CTF-sourced ranges (the #1695 path); the
Mission Control entry points (#1696) extend the same mechanism to
mission-control-sourced ranges, resolving the caller's own active range
so the presentation layer never handles range pks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cms.exceptions import CMSError
from shared.enums import RangeSource, ResourceStatus

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from cms.models import Request
    from shared.remote_access import OpenVpnProfile

_CTF_RANGE_NOT_FOUND = "CTF range not found"
_RANGE_NOT_FOUND = "Range not found"


class CtfOpenVpnProfileNotFound(CMSError):
    """The caller has no matching CTF range (non-enumerating)."""


class CtfOpenVpnProfileConflict(CMSError):
    """The owned CTF range is not currently eligible for profile delivery."""


class CtfOpenVpnProfileUnavailable(CMSError):
    """The profile binding or provider material cannot currently be resolved."""


def _load_owned_ctf_range_request(user: User, range_instance_pk: int) -> Request:
    """Load a READY, caller-owned CTF range's provisioning request or raise non-enumerating errors."""
    from cms.models import RangeInstance

    if getattr(user, "id", None) is None:
        raise CtfOpenVpnProfileNotFound(_CTF_RANGE_NOT_FOUND)
    range_instance = (
        RangeInstance.objects.select_related("request")
        .filter(pk=range_instance_pk, user_id=user.id, range_source=RangeSource.CTF.value)
        .first()
    )
    if range_instance is None:
        raise CtfOpenVpnProfileNotFound(_CTF_RANGE_NOT_FOUND)
    if range_instance.status != ResourceStatus.READY.value:
        raise CtfOpenVpnProfileConflict("CTF range is not ready")
    if range_instance.request is None:
        raise CtfOpenVpnProfileUnavailable("CTF range access is unavailable")
    return range_instance.request


def get_ctf_openvpn_profile(user: User, range_instance_pk: int) -> OpenVpnProfile:
    """Return the current profile after CMS ownership, provenance and state checks."""
    cms_request = _load_owned_ctf_range_request(user, range_instance_pk)
    from cms import services as cms_services
    from engine.services import VpnProfileConflict, VpnProfileNotFound, VpnProfileUnavailable

    try:
        return cms_services.engine_get_openvpn_profile(user, cms_request.request_id)
    except VpnProfileNotFound as exc:
        raise CtfOpenVpnProfileNotFound(_CTF_RANGE_NOT_FOUND) from exc
    except VpnProfileConflict as exc:
        raise CtfOpenVpnProfileConflict("CTF range VPN profile is not ready") from exc
    except VpnProfileUnavailable as exc:
        raise CtfOpenVpnProfileUnavailable("CTF range VPN profile is unavailable") from exc


def has_ctf_openvpn_profile(user: User, range_instance_pk: int) -> bool:
    """Return a safe capability bit without exposing binding metadata."""
    try:
        cms_request = _load_owned_ctf_range_request(user, range_instance_pk)
    except CMSError:
        return False
    from cms import services as cms_services

    return cms_services.engine_has_openvpn_profile(user, cms_request.request_id)


def _load_own_mission_control_range_request(user: User) -> Request:
    """Load the caller's READY mission-control range request, or raise non-enumerating errors."""
    from cms.models import RangeInstance
    from shared.enums import ACTIVE_STATUSES

    if getattr(user, "id", None) is None:
        raise CtfOpenVpnProfileNotFound(_RANGE_NOT_FOUND)
    range_instance = (
        RangeInstance.objects.select_related("request")
        .filter(
            user_id=user.id,
            range_source=RangeSource.MISSION_CONTROL.value,
            status__in=[s.value for s in ACTIVE_STATUSES],
        )
        .order_by("-id")
        .first()
    )
    if range_instance is None:
        raise CtfOpenVpnProfileNotFound(_RANGE_NOT_FOUND)
    if range_instance.status != ResourceStatus.READY.value:
        raise CtfOpenVpnProfileConflict("Range is not ready")
    if range_instance.request is None:
        raise CtfOpenVpnProfileUnavailable("Range access is unavailable")
    return range_instance.request


def get_own_mission_control_openvpn_profile(user: User) -> OpenVpnProfile:
    """Return the caller's active mission-control range profile (#1696)."""
    cms_request = _load_own_mission_control_range_request(user)
    from cms import services as cms_services
    from engine.services import VpnProfileConflict, VpnProfileNotFound, VpnProfileUnavailable

    try:
        return cms_services.engine_get_openvpn_profile(user, cms_request.request_id)
    except VpnProfileNotFound as exc:
        raise CtfOpenVpnProfileNotFound(_RANGE_NOT_FOUND) from exc
    except VpnProfileConflict as exc:
        raise CtfOpenVpnProfileConflict("Range VPN profile is not ready") from exc
    except VpnProfileUnavailable as exc:
        raise CtfOpenVpnProfileUnavailable("Range VPN profile is unavailable") from exc


def has_own_mission_control_openvpn_profile(user: User) -> bool:
    """Return a safe capability bit for the caller's active mission-control range."""
    try:
        cms_request = _load_own_mission_control_range_request(user)
    except CMSError:
        return False
    from cms import services as cms_services

    return cms_services.engine_has_openvpn_profile(user, cms_request.request_id)
