"""Cognito group capture from verified OIDC claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

COGNITO_GROUPS_CLAIM = "cognito:groups"
SESSION_KEY = "cognito_groups"


def normalize_cognito_groups(raw: object) -> list[str]:
    """Return a normalized list of Cognito group names from a claim value."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(group) for group in raw if group]
    return []


def persist_cognito_groups(user: User, groups: list[str], request: HttpRequest | None = None) -> None:
    """Persist Cognito groups on the user profile and mirror them in session."""
    from management.services import get_user_profile

    profile = get_user_profile(user)
    if profile.cognito_groups != groups:
        profile.cognito_groups = groups
        profile.save(update_fields=["cognito_groups"])

    if request is not None and hasattr(request, "session"):
        request.session[SESSION_KEY] = groups


def sync_cognito_groups_from_claims(
    user: User,
    claims: Mapping[str, object],
    request: HttpRequest | None = None,
) -> None:
    """Update stored Cognito groups from verified OIDC claims."""
    groups = normalize_cognito_groups(claims.get(COGNITO_GROUPS_CLAIM))
    persist_cognito_groups(user, groups, request)


class ManagementCognitoGroupsProvider:
    """Resolves the Cognito groups last captured on a user's ``UserProfile``.

    Satisfies ``shared.audit.groups_port.CognitoGroupsProvider`` (#1374
    fix-forward). ``config`` is the composition layer allowed to import
    ``management.services`` (``shared`` is not, per the layer contract), so
    this concrete adapter -- and its binding to the neutral port in
    ``config.apps.PortalConfig.ready`` -- is what lets
    ``shared.audit.access.cognito_groups_for_request`` fall back to the
    stored profile when a session predates Cognito-group capture.
    """

    @staticmethod
    def groups_for_user(user: User) -> list[str]:
        """Return the Cognito groups stored on ``user``'s profile."""
        from management.services import get_user_profile

        profile = get_user_profile(user)
        return list(profile.cognito_groups or [])


cognito_groups_provider = ManagementCognitoGroupsProvider()
