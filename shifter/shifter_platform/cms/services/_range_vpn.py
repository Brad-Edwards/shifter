"""CTF-only CMS authorization boundary for participant OpenVPN profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cms.exceptions import CMSError
from shared.enums import RangeSource, ResourceStatus

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from cms.models import Request
    from shared.remote_access import OpenVpnProfile

_CTF_RANGE_NOT_FOUND = "CTF range not found"


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
