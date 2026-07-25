"""Audit-log read-access Cognito-group policy (#1374 fix-forward).

Restores the pre-#1374 risk-register-owned compound authorization for the
platform audit-read API under an audit-owned name (the retired
``risk_register.access`` module). Only session principals are considered: a
platform API token is never granted the audit scope (ADR-029), so
``shared.api.permissions.HasAuditLogCognitoGroup`` rejects token requests
before this module is consulted.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from shared.audit.groups_port import get_cognito_groups_provider
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


def allowed_audit_log_cognito_groups() -> frozenset[str]:
    """Return the configured Cognito groups that grant audit-log read access."""
    return frozenset(getattr(settings, "AUDIT_LOG_ALLOWED_COGNITO_GROUPS", ()))


def cognito_groups_for_request(request: HttpRequest, user: User) -> list[str]:
    """Return ``user``'s Cognito groups: session first, then the bound profile provider.

    The session is preferred when present (even an empty list) so a group
    change reflected mid-session is not shadowed by a stale profile snapshot;
    the provider fallback only fires when the session has never captured
    groups at all (e.g. a session that predates group capture).
    """
    session = getattr(request, "session", None)
    session_groups = session.get("cognito_groups") if session is not None else None
    if session_groups is not None:
        return list(session_groups)
    return list(get_cognito_groups_provider().groups_for_user(user))


def log_audit_log_groups_unconfigured(user: User) -> None:
    """Emit an operator-legible warning when the allow-list is unconfigured.

    Fail-closed denial alone is not diagnosable from a 403 response; this
    gives an operator a clear line in the application log to explain why a
    fresh install denies every principal, staff included.
    """
    identity = getattr(user, "email", "") or getattr(user, "username", "") or str(user)
    logger.warning(
        "AUDIT_LOG_ALLOWED_COGNITO_GROUPS is unset; denying audit log read for %s",
        safe_log_value(identity),
    )
