"""Risk register Cognito group access policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from risk_register.models import APIKey
from shared.api_tokens.models import ApiToken

if TYPE_CHECKING:
    from django.http import HttpRequest


def allowed_risk_register_cognito_groups() -> frozenset[str]:
    """Return configured Cognito groups that grant risk register access."""
    return frozenset(getattr(settings, "RISK_REGISTER_ALLOWED_COGNITO_GROUPS", ()))


def cognito_groups_for_user(user) -> list[str]:
    """Return Cognito groups last captured for ``user``."""
    from management.services import get_user_profile

    profile = get_user_profile(user)
    return list(profile.cognito_groups or [])


def _groups_intersect_allowed(groups: list[str]) -> bool:
    allowed = allowed_risk_register_cognito_groups()
    if not allowed:
        return False
    return bool(set(groups) & allowed)


def principal_has_risk_register_access(request: HttpRequest) -> bool:
    """Return True when the request principal is in an allowed Cognito group."""
    if not allowed_risk_register_cognito_groups():
        return False

    auth = getattr(request, "auth", None)
    user = getattr(request, "user", None)

    if isinstance(auth, (ApiToken, APIKey)):
        owner = getattr(auth, "created_by", None)
        if owner is None:
            return False
        return _groups_intersect_allowed(cognito_groups_for_user(owner))

    if user and user.is_authenticated:
        session_groups = request.session.get("cognito_groups")
        if session_groups is not None:
            return _groups_intersect_allowed(list(session_groups))
        return _groups_intersect_allowed(cognito_groups_for_user(user))

    return False
