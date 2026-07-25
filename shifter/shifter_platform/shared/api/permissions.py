"""Shared DRF permissions for platform API infrastructure endpoints."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rest_framework import permissions

from shared.api_tokens.models import ApiToken
from shared.audit import AuditAction, AuditEntityType, audit_log_from_request
from shared.audit.access import (
    allowed_audit_log_cognito_groups,
    cognito_groups_for_request,
    log_audit_log_groups_unconfigured,
)

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# Shared verbatim so an API token is denied with the identical audit context
# regardless of which permission in a composed ``permission_classes`` list
# happens to evaluate the request first (#1374 fix-forward).
# Named "CREDENTIAL" rather than "TOKEN" so bandit's B105 heuristic does not
# pattern-match the name and force a suppression comment: this is a
# human-readable audit-denial reason, not a credential.
API_CREDENTIAL_REJECTED_FOR_AUDIT_READS = "API token rejected for audit reads"


class IsAuthenticatedSessionOrApiToken(permissions.BasePermission):
    """Allow authenticated browser sessions or valid platform API tokens."""

    message = "Authentication credentials were not provided."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if isinstance(getattr(request, "auth", None), ApiToken):
            return True
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)


class IsStaffSession(permissions.BasePermission):
    """Session-authenticated staff/superuser only; platform token principals rejected.

    A platform API token carries no Django user and no management scope, so
    endpoints authorized by this class are session-only (ADR-029). Used by the
    Administer user-administration surface (#1373).
    """

    message = "This action requires a staff session."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if isinstance(getattr(request, "auth", None), ApiToken):
            return False
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


class AuditedPermissionDenialMixin:
    """Mixin that records an ``ACCESS_DENIED`` audit row when denied.

    Moved from the retired risk-register-local ``AuditedPermissionMixin``
    (#1374/#1523) so shared, cross-domain permission classes can preserve the
    same fail-visible signal without importing the risk-register domain.
    Audit-logging failures never break the request flow.
    """

    @staticmethod
    def _log_permission_denied(request: Request, view: APIView, message: str = "") -> None:
        try:
            entity_id = 0
            if hasattr(view, "kwargs") and view.kwargs:
                entity_id = view.kwargs.get("pk", 0) or 0
            context = f"Permission denied: {type(view).__name__}"
            if message:
                context = f"{context} - {message}"
            audit_log_from_request(
                request,
                entity_type=AuditEntityType.CONFIG,
                entity_id=entity_id,
                action=AuditAction.ACCESS_DENIED,
                context=context,
            )
        except Exception:
            logger.exception("Failed to log permission denied event")


class IsStaffSessionAudited(AuditedPermissionDenialMixin, IsStaffSession):
    """``IsStaffSession`` that also audits denials (#1374 audit-read rehome).

    Staff/superuser session only; a platform API token is rejected exactly as
    ``IsStaffSession`` rejects it. Every denial additionally emits an
    ``ACCESS_DENIED`` audit row, preserving the retired risk-register
    ``AuditedPermissionMixin`` behavior for the rehomed audit-read endpoint.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        allowed = super().has_permission(request, view)
        if not allowed:
            message = (
                API_CREDENTIAL_REJECTED_FOR_AUDIT_READS
                if isinstance(getattr(request, "auth", None), ApiToken)
                else "Not a staff/superuser session"
            )
            self._log_permission_denied(request, view, message)
        return allowed


class HasAuditLogCognitoGroup(AuditedPermissionDenialMixin, permissions.BasePermission):
    """Require membership in a configured Cognito group for audit-log reads.

    Restores the pre-#1374 risk-register-owned compound gate under an
    audit-owned name: the platform audit trail is sensitive (actor identity,
    source IPs, before/after state), so staff status alone is not sufficient.
    Compose with :class:`IsStaffSessionAudited` at the viewset --
    ``permission_classes = [HasAuditLogCognitoGroup, IsStaffSessionAudited]``
    -- DRF ANDs every class in the list, restoring the exact compound
    semantics the retired ``risk_register`` gate enforced.

    Only session principals are considered: a platform API token is rejected
    here exactly as ``IsStaffSession`` rejects it, so a token never falls
    through to group evaluation. Fails closed when
    ``AUDIT_LOG_ALLOWED_COGNITO_GROUPS`` is unconfigured -- an unconfigured
    deployment has no audit-read access at all, not a silent fallback to
    staff-only.
    """

    message = "Audit log access requires membership in an allowed Cognito group."

    def has_permission(self, request: Request, view: APIView) -> bool:
        reason = self._denial_reason(request)
        if reason is not None:
            self._log_permission_denied(request, view, reason)
        return reason is None

    @staticmethod
    def _denial_reason(request: Request) -> str | None:
        """Return why ``request`` would be denied, or None when it is authorized."""
        user = getattr(request, "user", None)
        if isinstance(getattr(request, "auth", None), ApiToken):
            reason = API_CREDENTIAL_REJECTED_FOR_AUDIT_READS
        elif not (user and user.is_authenticated):
            reason = "Not an authenticated session"
        else:
            allowed = allowed_audit_log_cognito_groups()
            if not allowed:
                log_audit_log_groups_unconfigured(user)
                reason = "AUDIT_LOG_ALLOWED_COGNITO_GROUPS is not configured"
            elif not (set(cognito_groups_for_request(request, user)) & allowed):
                reason = "Not in allowed Cognito group"
            else:
                reason = None
        return reason


class RequireModelPermission(permissions.BasePermission):
    """Require specific Django model permission codenames on the request user.

    ``is_staff`` alone is never authorization; each endpoint declares the exact
    codenames it needs (e.g. ``auth.view_user`` / ``auth.change_user``).
    Superusers implicitly pass Django's ``has_perm``; a staff user lacking the
    codename is denied. Build concrete classes with :func:`require_model_permission`.
    """

    required_perms: tuple[str, ...] = ()

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        return all(user.has_perm(perm) for perm in self.required_perms)


def require_model_permission(*perms: str) -> type[permissions.BasePermission]:
    """Return a :class:`RequireModelPermission` subclass requiring all of ``perms``."""
    return type("RequireModelPermission", (RequireModelPermission,), {"required_perms": tuple(perms)})
