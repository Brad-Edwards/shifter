"""Event-staff roles and authority-topology mutations (CTF-607, #1922).

The owning organizer (``CTFEvent.created_by``) is the single canonical owner and
always holds every capability. ``CTFEventStaff`` rows grant other organizer-tier
users a bounded slice of one event's management surface, keyed by a closed
:class:`ctf.enums.EventCapability` vocabulary and a fail-closed role map:

- ``moderator``: ``participants`` and ``notifications``
- ``judge``: ``awards`` and ``submissions``
- ``co_organizer``: every operational capability (configuration, challenges,
  participants, teams, ranges, scoring, notifications, awards, submissions,
  content, lifecycle, deletion)

Authority-topology operations — listing/assigning/re-roling/revoking staff and
transferring canonical ownership — are NOT capabilities. They use an explicit
owner predicate so no role map can ever grant them, which is what keeps a
co-organizer from escalating itself or removing the owner's control. Unknown
roles and unknown capabilities deny; there is no wildcard grant.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth.models import User
from django.db import transaction

from ctf.enums import EventCapability, EventStaffRole
from ctf.exceptions import CTFNotFoundError, CTFValidationError
from ctf.models import CTFEvent, CTFEventStaff
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from uuid import UUID

    from django.contrib.auth.models import AnonymousUser
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

# Fail-closed role -> capability map. Co-organizers receive the full operational
# set as an explicit, enumerated grant (never a wildcard), so a newly added
# capability is authorized only when intentionally added here.
_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    EventStaffRole.MODERATOR.value: frozenset(
        {EventCapability.PARTICIPANTS.value, EventCapability.NOTIFICATIONS.value}
    ),
    EventStaffRole.JUDGE.value: frozenset({EventCapability.AWARDS.value, EventCapability.SUBMISSIONS.value}),
    EventStaffRole.CO_ORGANIZER.value: frozenset(cap.value for cap in EventCapability),
}

# The complete closed capability vocabulary. A capability outside this set is
# unknown (misspelled, or introduced without a role-map update) and denies for
# everyone — including the owner — so the vocabulary fails closed rather than
# silently authorizing an owner request against a typo (#1922 review).
_ALL_CAPABILITIES: frozenset[str] = frozenset(cap.value for cap in EventCapability)


def _live_staff_role(event: CTFEvent, user_id: int) -> str | None:
    """Return the live (non-revoked) staff role for ``user_id`` on ``event``, or None."""
    return (
        CTFEventStaff.objects.filter(event=event, user_id=user_id, deleted_at__isnull=True)
        .values_list("role", flat=True)
        .first()
    )


def actor_is_active_ctf_organizer(user_id: int | None) -> bool:
    """Return whether ``user_id`` is an active account with the global CTF organizer role.

    Staff-derived authority is only valid while the account still holds the
    platform CTF Organizer role: a live ``CTFEventStaff`` row must never be an
    ambient source of authority after the global role is revoked or the account
    is deactivated (#1922 review — stale-row bypass). The owner branch does not
    use this; canonical ownership is a separate invariant.
    """
    if user_id is None:
        return False
    from ctf.bridges import get_user_role

    user = User.objects.filter(pk=user_id, is_active=True).first()
    return user is not None and get_user_role(user).is_ctf_organizer


def eligible_co_organizer_ids(event: CTFEvent) -> list[int]:
    """User ids of live full co-organizers on ``event`` who are still eligible.

    Filters live ``co_organizer`` rows to accounts that remain active and hold
    the global CTF Organizer role, so a demoted account stops appearing in
    organizer-directed recipient and subscription projections (#1922 review).
    """
    from shared.auth import CTF_ORGANIZER_GROUP

    return list(
        CTFEventStaff.objects.filter(
            event=event,
            role=EventStaffRole.CO_ORGANIZER.value,
            deleted_at__isnull=True,
            user__is_active=True,
            user__groups__name=CTF_ORGANIZER_GROUP,
        ).values_list("user_id", flat=True)
    )


def actor_is_event_owner(actor: User | AnonymousUser | int, event: CTFEvent) -> bool:
    """Return whether ``actor`` is the exact canonical owner of ``event``."""
    actor_pk = actor if isinstance(actor, int) else actor.pk
    return actor_pk is not None and event.created_by_id == actor_pk


def actor_can_exercise(actor_id: int | None, event: CTFEvent, capability: str) -> bool:
    """Return whether ``actor_id`` may exercise ``capability`` on ``event`` (fail closed).

    An unknown capability denies for everyone, the owner included, so a typo or a
    newly introduced operation missing from the role map cannot slip through on
    the owner branch (#1922 review).
    """
    if actor_id is None or str(capability) not in _ALL_CAPABILITIES:
        return False
    if event.created_by_id == actor_id:
        return True
    role = _live_staff_role(event, actor_id)
    # A live staff row grants authority only when its role maps the capability AND
    # the account still holds the global CTF Organizer role and is active (#1922
    # review — a demoted account must not retain authority through a stale row).
    return (
        role is not None
        and str(capability) in _ROLE_CAPABILITIES.get(role, frozenset())
        and actor_is_active_ctf_organizer(actor_id)
    )


def actor_has_event_capability(actor: User | AnonymousUser, event: CTFEvent, capability: str) -> bool:
    """Return whether ``actor`` may exercise ``capability`` on ``event``.

    The owning organizer always may; otherwise a live staff row whose role
    grants the capability is required.
    """
    return actor_can_exercise(actor.pk, event, capability)


def event_access_projection(actor_id: int | None, event: CTFEvent) -> tuple[str | None, list[str]]:
    """Return ``(access_role, advisory_capabilities)`` for ``actor_id`` on ``event``.

    Server-derived presentation hint (#1922): ``access_role`` is ``owner``,
    ``co_organizer``, ``moderator``, ``judge``, or ``None``; the capability list
    is the sorted set the role grants. A UI hides/disables controls the actor
    cannot use, but the hint never authorizes — every mutation is still checked
    server-side.
    """
    if actor_id is None:
        return None, []
    if event.created_by_id == actor_id:
        return "owner", sorted(cap.value for cap in EventCapability)
    role = _live_staff_role(event, actor_id)
    return role, sorted(_ROLE_CAPABILITIES.get(role, frozenset())) if role else []


def _resolve_owned_event_for_staff(event_id: UUID, actor: User | AnonymousUser) -> CTFEvent:
    """Load and lock the event, requiring exact ownership (authority topology is owner-only).

    Must run inside a transaction: the ``select_for_update`` row lock makes the
    event the stable per-event mutex so concurrent assign/re-role/revoke/transfer
    commands authorize against the same owner.
    """
    try:
        event = CTFEvent.objects.select_for_update().get(pk=event_id, deleted_at__isnull=True)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(f"Event {event_id} not found", details={"event_id": str(event_id)}) from None
    if not actor_is_event_owner(actor, event):
        raise CTFValidationError(
            "Only the event organizer may manage staff",
            code="CTF_PERMISSION_DENIED",
        )
    return event


@transaction.atomic
def assign_event_staff(event_id: UUID, actor: User, email: str, role: str) -> CTFEventStaff:
    """Assign (or re-role) a staff member on an owned event.

    Owner-only and locked. The target is resolved by email and must be an active
    organizer-tier platform user — the organizer API surface requires that
    platform role, so a standard user could never exercise the delegation.
    Assigning an existing staff member changes their role. Strictly audited.
    """
    from ctf.bridges import get_user_role
    from ctf.services.audit import audit_event_staff_change

    if role not in _ROLE_CAPABILITIES:
        raise CTFValidationError(
            "Invalid staff role",
            code="CTF_INVALID_STAFF_ROLE",
            details={"role": role, "valid": sorted(_ROLE_CAPABILITIES)},
        )
    event = _resolve_owned_event_for_staff(event_id, actor)
    target = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if target is None:
        raise CTFNotFoundError("No active user with that email", details={"email": email.strip()})
    if target.pk == event.created_by_id:
        raise CTFValidationError(
            "The event organizer already holds every capability",
            code="CTF_STAFF_IS_ORGANIZER",
        )
    if not get_user_role(target).is_ctf_organizer:
        raise CTFValidationError(
            "Staff members need the CTF organizer platform role",
            code="CTF_STAFF_ROLE_REQUIRED",
        )
    staff = CTFEventStaff.objects.filter(event=event, user=target, deleted_at__isnull=True).first()
    previous_role = None if staff is None else staff.role
    if staff is None:
        staff = CTFEventStaff.objects.create(event=event, user=target, role=role)
        action = "assigned"
    elif staff.role != role:
        staff.role = role
        staff.save(update_fields=["role", "updated_at"])
        action = "reroled"
    else:
        # Idempotent re-submission of the current role performs no write, so it
        # must not fabricate a strict audit event or log a mutation that never
        # happened (#1922 review).
        return staff
    audit_event_staff_change(
        actor_id=actor.pk,
        event_id=event.pk,
        target_user_id=target.pk,
        action=action,
        role=role,
        previous_role=previous_role,
    )
    logger.info(
        "%s staff %s on event %s as %s",
        "Assigned" if action == "assigned" else "Re-roled",
        target.pk,
        event.pk,
        role,
    )
    return staff


@transaction.atomic
def revoke_event_staff(event_id: UUID, actor: User, user_id: int) -> bool:
    """Remove a staff assignment from an owned event (owner-only, locked, audited).

    The staff-revocation command never targets ``created_by`` (the owner holds no
    staff row), so it can never leave an event without an owner.
    """
    from ctf.services.audit import audit_event_staff_change

    event = _resolve_owned_event_for_staff(event_id, actor)
    staff = CTFEventStaff.objects.filter(event=event, user_id=user_id, deleted_at__isnull=True).first()
    if staff is None:
        raise CTFNotFoundError("Staff assignment not found", details={"user_id": str(user_id)})
    revoked_role = staff.role
    staff.delete(soft=True)
    audit_event_staff_change(
        actor_id=actor.pk,
        event_id=event.pk,
        target_user_id=user_id,
        action="revoked",
        previous_role=revoked_role,
    )
    logger.info("Revoked staff %s on event %s", safe_log_value(user_id), event.pk)
    return True


@transaction.atomic
def transfer_event_ownership(event_id: UUID, actor: User, new_owner_user_id: int) -> CTFEvent:
    """Transfer canonical ownership to an existing co-organizer (owner-only, locked, audited).

    One atomic command: the target must already be a live co-organizer; in the
    same transaction the target becomes ``created_by``, its now-redundant staff
    row is soft-deleted, and the previous owner is retained as a co-organizer.
    The non-null owner invariant is never transiently broken. Ownership transfer
    does not move participant accounts, teams, ranges, tokens, or provider
    credentials — those keep their existing product-specific owners.
    """
    from ctf.services.audit import audit_event_ownership_transferred

    event = _resolve_owned_event_for_staff(event_id, actor)
    previous_owner_id = event.created_by_id
    if new_owner_user_id == previous_owner_id:
        raise CTFValidationError(
            "The target already owns this event",
            code="CTF_ALREADY_OWNER",
        )
    target_staff = (
        CTFEventStaff.objects.select_for_update()
        .filter(
            event=event,
            user_id=new_owner_user_id,
            role=EventStaffRole.CO_ORGANIZER.value,
            deleted_at__isnull=True,
        )
        .first()
    )
    if target_staff is None:
        raise CTFValidationError(
            "Ownership can only transfer to a current co-organizer",
            code="CTF_TRANSFER_TARGET_INVALID",
        )
    # Revalidate the target's CURRENT platform eligibility under the lock (#1922
    # review F2): a co-organizer assignment can go stale if the user is later
    # deactivated or loses the CTF organizer role. Transferring to a stale
    # assignment would make an ineligible account the canonical owner and could
    # strand the event with no principal able to manage staff or transfer back.
    from ctf.bridges import get_user_role

    target = User.objects.filter(pk=new_owner_user_id, is_active=True).first()
    if target is None or not get_user_role(target).is_ctf_organizer:
        raise CTFValidationError(
            "Ownership can only transfer to an active CTF organizer",
            code="CTF_TRANSFER_TARGET_INELIGIBLE",
        )
    event.created_by_id = new_owner_user_id
    event.save(update_fields=["created_by", "updated_at"])
    # The new owner no longer needs a staff row; the previous owner keeps full
    # access as a co-organizer so the change is reversible by the new owner.
    target_staff.delete(soft=True)
    CTFEventStaff.objects.create(
        event=event,
        user_id=previous_owner_id,
        role=EventStaffRole.CO_ORGANIZER.value,
    )
    audit_event_ownership_transferred(
        actor_id=actor.pk,
        event_id=event.pk,
        previous_owner_id=previous_owner_id,
        new_owner_id=new_owner_user_id,
    )
    logger.info("Transferred ownership of event %s to user %s", event.pk, safe_log_value(new_owner_user_id))
    return event


def list_event_staff(event_id: UUID) -> QuerySet[CTFEventStaff]:
    """List live staff assignments for an event."""
    return (
        CTFEventStaff.objects.filter(event_id=event_id, deleted_at__isnull=True)
        .select_related("user")
        .order_by("created_at")
    )
