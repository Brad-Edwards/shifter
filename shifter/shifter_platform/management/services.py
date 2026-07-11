"""Management service interface.

Platform administration for Shifter platform.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from risk_register.models import AuditLog
from risk_register.services import AuditEvent, audit_log
from shared.constants import USER_CANNOT_BE_NONE
from shared.log_sanitize import safe_log_fingerprint, safe_log_value

from .models import ActivityLog, UserProfile

# SonarCloud S1192: extracted duplicated string literals.
USER_PK_REQUIRED_MSG = "user must have a primary key"

if TYPE_CHECKING:
    from uuid import UUID

    from django.contrib.auth.models import User
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def log_activity(action: str, user: User | None, **metadata: Any) -> None:
    """Log an activity for audit trail.

    DEPRECATED: Use risk_register.services.audit_log() instead.
    This function is retained for backward compatibility only.

    Args:
        action: Action identifier (e.g., "range_launched", "agent_uploaded")
        user: User who performed the action, or None for system actions
        **metadata: Additional context to store with the log entry

    Raises:
        TypeError: If action is not a string
        ValueError: If action is empty or user is unsaved
    """
    if not isinstance(action, str):
        raise TypeError("action must be a string")
    if not action.strip():
        raise ValueError("action cannot be empty")
    if user is not None and user.pk is None:
        raise ValueError(USER_PK_REQUIRED_MSG)

    user_display = safe_log_value(user.email) if user else "anonymous"

    try:
        ActivityLog.log(action, user=user, **metadata)
        logger.debug("Logged activity '%s' for user %s", action, user_display)
    except Exception:
        logger.exception("Failed to log activity '%s' for user %s", action, user_display)
        raise


def get_user_profile(user: User) -> UserProfile:
    """Get or create the profile for a user.

    Args:
        user: The user to get profile for

    Returns:
        UserProfile instance for the user

    Raises:
        TypeError: If user is None
        ValueError: If user has no primary key (unsaved)
    """
    if user is None:
        raise TypeError(USER_CANNOT_BE_NONE)
    if user.pk is None:
        raise ValueError(USER_PK_REQUIRED_MSG)

    try:
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            logger.debug("Created new profile for user %s", safe_log_value(user.email))
        else:
            logger.debug("Retrieved profile for user %s", safe_log_value(user.email))
        return profile
    except Exception:
        logger.exception("Failed to get/create profile for user %s", safe_log_value(user.email))
        raise


def mark_user_deleted(user: User, admin_user: User | None = None) -> None:
    """Soft delete a user by setting deleted_at timestamp.

    Creates profile if it doesn't exist.

    Args:
        user: The user to mark as deleted
        admin_user: The admin user performing the deletion (for audit)

    Raises:
        TypeError: If user is None
        ValueError: If user has no primary key (unsaved)
    """
    profile = get_user_profile(user)

    if profile.is_deleted:
        logger.warning("User %s is already deleted, updating timestamp", safe_log_value(user.email))

    try:
        profile.deleted_at = timezone.now()
        profile.save(update_fields=["deleted_at"])

        # Audit log user deletion
        audit_log(
            AuditEvent(
                entity_type=AuditLog.EntityType.USER,
                entity_id=user.id,
                action=AuditLog.Action.DELETE,
                actor_type=AuditLog.ActorType.USER if admin_user else AuditLog.ActorType.SYSTEM,
                actor_id=admin_user.id if admin_user else None,
                previous_state={"email": user.email},
            )
        )

        logger.debug("Marked user %s as deleted", safe_log_value(user.email))
    except Exception:
        logger.exception("Failed to mark user %s as deleted", safe_log_value(user.email))
        raise


def create_user_profile(user: User) -> None:
    """Create a UserProfile for a user.

    Args:
        user: The user to create profile for

    Raises:
        TypeError: If user is None
        ValueError: If user has no primary key (unsaved)
    """
    if user is None:
        raise TypeError(USER_CANNOT_BE_NONE)
    if user.pk is None:
        raise ValueError(USER_PK_REQUIRED_MSG)

    try:
        UserProfile.objects.create(user=user)
        logger.debug("Created profile for user %s", safe_log_value(user.email))
    except Exception:
        logger.exception("Failed to create profile for user %s", safe_log_value(user.email))
        raise


def save_user_profile(user: User) -> None:
    """Ensure a UserProfile exists for a user.

    Args:
        user: The user to ensure profile for

    Raises:
        TypeError: If user is None
        ValueError: If user has no primary key (unsaved)
    """
    if user is None:
        raise TypeError(USER_CANNOT_BE_NONE)
    if user.pk is None:
        raise ValueError(USER_PK_REQUIRED_MSG)

    try:
        UserProfile.objects.get_or_create(user=user)
        logger.debug("Ensured profile for user %s", safe_log_value(user.email))
    except Exception:
        logger.exception("Failed to ensure profile for user %s", safe_log_value(user.email))
        raise


def resolve_user_by_provider_identity(issuer: str, subject: str) -> QuerySet[User]:
    """Subject-first resolution of the user bound to a verified provider identity (issue #1521).

    The canonical, provider-neutral account-resolution seam (ADR-009-R6): both
    production auth adapters call this instead of re-implementing the
    issuer/subject lookup, so the identity-key query lives in the management
    persistence layer next to :func:`bind_provider_identity` rather than being
    duplicated in ``config.oidc`` and ``config.identity_platform``.

    Because ``UserProfile.cognito_sub`` is unique, at most one profile matches a
    given ``subject``; it resolves that user when the stored issuer is either the
    verified issuer (an exact bind) or empty (a legacy row not yet bound to an
    issuer). A drifted issuer or an unknown subject yields an empty queryset,
    leaving the caller's provider-native email fallback (the unbound/first-
    bootstrap seam) and :func:`bind_provider_identity`'s fail-closed compare to
    run. Resolution never creates, mutates, or binds; the caller performs
    creation and binding inside one atomic, row-locked security mutation.

    Returns a queryset (never ``None``) so the OIDC adapter can hand it straight
    to ``mozilla-django-oidc``'s multi/one/none handling while the Identity
    Platform adapter takes ``.first()``.
    """
    user_model = get_user_model()
    if not subject or not subject.strip():
        return user_model.objects.none()
    return user_model.objects.filter(
        Q(profile__issuer=issuer) | Q(profile__issuer=""),
        profile__cognito_sub=subject,
    )


class BindingConflictError(RuntimeError):
    """Raised when presented provider identity evidence conflicts with bound state.

    An authentication failure that callers must reject, never resolve
    automatically: :func:`bind_provider_identity` never overwrites, backfills,
    or "heals" a stored ``(issuer, subject)`` binding (issue #1521).
    """


class BindOutcome(StrEnum):
    """Result of a :func:`bind_provider_identity` call."""

    UNCHANGED = "unchanged"
    ISSUER_ACQUIRED = "issuer_acquired"
    BOUND = "bound"


def _require_bind_inputs(user: User, issuer: str, subject: str) -> None:
    """Validate :func:`bind_provider_identity` inputs, raising on any bad value."""
    if user is None:
        raise TypeError(USER_CANNOT_BE_NONE)
    if user.pk is None:
        raise ValueError(USER_PK_REQUIRED_MSG)
    if not issuer or not issuer.strip():
        raise ValueError("issuer cannot be empty")
    if not subject or not subject.strip():
        raise ValueError("subject cannot be empty")


def bind_provider_identity(user: User, issuer: str, subject: str) -> BindOutcome:
    """Bind-once/compare a verified provider ``(issuer, subject)`` tuple (issue #1521).

    Replaces the historical overwrite-style ``update_cognito_sub``. Runs under
    ``transaction.atomic()`` with ``select_for_update()`` on the profile row so
    concurrent logins for the same account cannot race past each other.

    Semantics, fail-closed:

    - exact ``(issuer, subject)`` already bound to this profile -> idempotent
      no-op (:attr:`BindOutcome.UNCHANGED`).
    - legacy row (empty issuer, matching subject) -> acquires the verified
      issuer (:attr:`BindOutcome.ISSUER_ACQUIRED`).
    - fully unbound profile (empty issuer, empty/null subject) -> binds once
      (:attr:`BindOutcome.BOUND`).
    - any other stored issuer/subject difference, or a uniqueness collision
      with a different user's profile, -> raises :class:`BindingConflictError`.
      Never overwrites, backfills, or "heals" a stored identity.

    Args:
        user: The user to bind.
        issuer: Verified, non-empty provider issuer (opaque, case-sensitive).
        subject: Verified, non-empty provider subject (opaque, case-sensitive).

    Raises:
        TypeError: If user is None.
        ValueError: If user has no primary key, or issuer/subject is blank.
        BindingConflictError: If the presented tuple conflicts with stored
            state, or collides with a different user's bound subject.
    """
    _require_bind_inputs(user, issuer, subject)

    profile = get_user_profile(user)
    try:
        with transaction.atomic():
            locked_profile = UserProfile.objects.select_for_update().get(pk=profile.pk)
            stored_issuer = locked_profile.issuer  # non-null (default ""); "" means unbound/legacy
            stored_subject = locked_profile.cognito_sub or ""

            if stored_issuer == issuer and stored_subject == subject:
                logger.debug("Provider identity already bound for user %s", safe_log_value(user.email))
                return BindOutcome.UNCHANGED

            if not stored_issuer and stored_subject == subject:
                locked_profile.issuer = issuer
                locked_profile.save(update_fields=["issuer"])
                logger.info(
                    "Legacy identity row for user %s acquired verified issuer (subject=%s)",
                    safe_log_value(user.email),
                    safe_log_fingerprint(subject),
                )
                return BindOutcome.ISSUER_ACQUIRED

            if not stored_issuer and not stored_subject:
                locked_profile.issuer = issuer
                locked_profile.cognito_sub = subject
                locked_profile.save(update_fields=["issuer", "cognito_sub"])
                logger.info(
                    "Bound provider identity for user %s (subject=%s)",
                    safe_log_value(user.email),
                    safe_log_fingerprint(subject),
                )
                return BindOutcome.BOUND

            raise BindingConflictError(
                "Presented provider identity conflicts with the identity already bound to this account"
            )
    except IntegrityError as exc:
        logger.warning(
            "Provider identity bind collided with a different account for user %s (subject=%s)",
            safe_log_value(user.email),
            safe_log_fingerprint(subject),
        )
        raise BindingConflictError("Presented provider identity is already bound to a different account") from exc


def set_active_ctf_event(user: User, event_id: UUID | None) -> None:
    """Set or clear the active CTF event for a user.

    Args:
        user: The user to update.
        event_id: CTF event UUID PK to set, or None to clear.
    """
    profile = get_user_profile(user)
    profile.active_ctf_event_id = event_id
    profile.save(update_fields=["active_ctf_event_id"])
