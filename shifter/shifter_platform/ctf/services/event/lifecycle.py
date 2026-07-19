"""CTF event lifecycle transitions (schedule, start, pause, end, cancel, archive, force delete)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import transaction

from ctf.enums import VALID_TRANSITIONS, EventStatus, validate_transition
from ctf.exceptions import CTFNotFoundError, CTFStateError, CTFValidationError
from ctf.models import CTFEvent
from ctf.services.event.crud import get_event
from ctf.services.event.scheduling import _cancel_event_tasks, _schedule_event_tasks
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def force_delete_event(
    event_id: UUID,
    actor: User,
    confirmation_name: str,
) -> dict[str, Any]:
    """Force-delete a CTF event and all associated resources.

    Permanently removes the event regardless of its current state. All child
    records (challenges, participants, submissions, scores, scheduled tasks,
    etc.) are cascade-deleted. Range instances are destroyed first via the CMS
    bridge.

    Args:
        event_id: UUID of the event to force-delete.
        actor: The user performing the deletion.
        confirmation_name: Must match the event name exactly (case-sensitive).

    Returns:
        Summary dict with event_id, event_name, ranges_destroyed, ranges_failed.

    Raises:
        CTFNotFoundError: If event doesn't exist.
        CTFValidationError: If confirmation_name doesn't match.
    """
    from ctf.models import CTFChallengeFile, CTFParticipant
    from ctf.s3 import delete_challenge_file
    from ctf.services.range.lifecycle import _destroy_single_range

    # Use all_objects so force delete works on soft-deleted events too
    try:
        event = CTFEvent.all_objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    if confirmation_name != event.name:
        raise CTFValidationError(
            "Confirmation name does not match event name",
            details={
                "expected": event.name,
                "provided": confirmation_name,
            },
        )

    event_name = event.name

    # Destroy range instances OUTSIDE the atomic block (external HTTP calls).
    # Query participants directly via all_objects to handle soft-deleted events
    # (cleanup_event_ranges uses CTFEvent.objects which skips soft-deleted).
    ranges_destroyed = 0
    ranges_failed = 0
    participants_with_ranges = CTFParticipant.all_objects.filter(
        event=event,
        range_instance_id__isnull=False,
    ).select_related("user")
    for participant in participants_with_ranges:
        try:
            _destroy_single_range(participant, participant.user)
            ranges_destroyed += 1
        except Exception:
            ranges_failed += 1
            logger.exception(
                "Failed to destroy range for participant %s during force delete",
                participant.pk,
            )

    # Delete S3 challenge file blobs before cascade removes the DB rows
    s3_keys = list(
        CTFChallengeFile.all_objects.filter(
            challenge__event=event,
        ).values_list("s3_key", flat=True)
    )
    for s3_key in s3_keys:
        try:
            delete_challenge_file(s3_key)
        except Exception:
            logger.exception("Failed to delete S3 object %s during force delete", s3_key)

    # Hard delete inside atomic block — Django CASCADE handles children
    with transaction.atomic():
        _cancel_event_tasks(event)
        event.delete(soft=False)

    logger.warning(
        "FORCE DELETE: Event %s (%s) permanently deleted by %s (pk=%s). Ranges destroyed: %d, ranges failed: %d.",
        safe_log_value(event_id),
        safe_log_value(event_name),
        safe_log_value(actor.email),
        actor.pk,
        ranges_destroyed,
        ranges_failed,
    )

    return {
        "event_id": str(event_id),
        "event_name": event_name,
        "ranges_destroyed": ranges_destroyed,
        "ranges_failed": ranges_failed,
    }


def start_event(event_id: UUID) -> CTFEvent:
    """Start a CTF event (transition to active).

    Args:
        event_id: UUID of the event.

    Returns:
        The updated CTFEvent instance.

    Raises:
        CTFNotFoundError: If event doesn't exist.
        CTFStateError: If event cannot be started.
    """
    logger.info("Starting CTF event %s", event_id)

    event = get_event(event_id)

    if event.status != EventStatus.REGISTRATION.value:
        raise CTFStateError(
            f"Cannot start event in {event.status} state",
            details={"event_id": str(event_id), "status": event.status},
        )

    event.status = EventStatus.ACTIVE.value
    event.save(update_fields=["status", "updated_at"])

    logger.info("Started CTF event %s", event_id)
    return event


def end_event(event_id: UUID) -> CTFEvent:
    """End a CTF event (transition to completed).

    Args:
        event_id: UUID of the event.

    Returns:
        The updated CTFEvent instance.

    Raises:
        CTFNotFoundError: If event doesn't exist.
        CTFStateError: If event cannot be ended.
    """
    logger.info("Ending CTF event %s", event_id)

    event = get_event(event_id)

    if event.status != EventStatus.ACTIVE.value:
        raise CTFStateError(
            f"Cannot end event in {event.status} state",
            details={"event_id": str(event_id), "status": event.status},
        )

    event.status = EventStatus.ENDED.value
    event.save(update_fields=["status", "updated_at"])

    logger.info("Ended CTF event %s", event_id)
    return event


def schedule_event(event: CTFEvent) -> bool:
    """Open registration for a draft event (transition to registration).

    Args:
        event: The CTFEvent to open registration for.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Opening registration for CTF event %s", event.id)

    try:
        _transition_event(event, EventStatus.REGISTRATION)
    except CTFStateError:
        logger.warning(
            "Cannot open registration for event %s: not in draft state (current: %s)",
            event.id,
            event.status,
        )
        return False

    _schedule_event_tasks(event)

    logger.info("Opened registration for CTF event %s", event.id)
    return True


# Registration opens when an event is scheduled; the public name preserves the
# original service vocabulary.
open_registration = schedule_event


def activate_event(event: CTFEvent) -> bool:
    """Activate a registration event (transition to active).

    For resuming a paused event, use ``resume_event`` instead.

    Args:
        event: The CTFEvent to activate.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Activating CTF event %s", event.id)

    if event.status != EventStatus.REGISTRATION.value:
        logger.warning(
            "Cannot activate event %s: not in registration state (current: %s)",
            event.id,
            event.status,
        )
        return False

    try:
        _transition_event(event, EventStatus.ACTIVE)
    except CTFStateError:
        return False

    logger.info("Activated CTF event %s", event.id)
    return True


def complete_event(event: CTFEvent) -> bool:
    """End an active event (transition to ended).

    If ``auto_cleanup`` is enabled, destroys all participant ranges
    to prevent orphaned cloud resources.

    Args:
        event: The CTFEvent to end.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Ending CTF event %s", event.id)

    try:
        _transition_event(event, EventStatus.ENDED)
    except CTFStateError:
        logger.warning(
            "Cannot end event %s: not in active state (current: %s)",
            event.id,
            event.status,
        )
        return False

    # Finalize the materialized leaderboard (issue #850) from authoritative
    # rows when the event ends, so the stored per-event scores are exact and
    # self-heal any incremental-maintenance drift before the board goes static.
    from ctf.services.scoring import recompute_event_leaderboard

    recompute_event_leaderboard(event.pk)

    # CTF-703: when a delayed CLEANUP_RANGES task pends, the post-event review
    # window applies and that task owns the destruction; cleaning here too
    # destroyed ranges hours early and then ran the task a second time.
    from ctf.services.event.scheduling import has_pending_cleanup_task

    if event.auto_cleanup and not has_pending_cleanup_task(event.pk):
        from ctf.services.range import cleanup_event_ranges

        result = cleanup_event_ranges(event.pk)
        logger.info("Auto-cleanup on event end %s: %s", event.id, result)

    # CTF-801: final-results email to participants; best-effort so a mail
    # outage never blocks the end transition.
    try:
        from ctf.services.notification import send_event_results

        send_event_results(event.pk)
    except Exception:
        logger.exception("Failed to send results for event %s", event.id)

    logger.info("Ended CTF event %s", event.id)
    return True


def cancel_event(event: CTFEvent) -> bool:
    """Cancel a CTF event.

    Cancellation is valid from draft, registration, active, or paused states.
    Always destroys all participant ranges to prevent orphaned cloud resources.

    Args:
        event: The CTFEvent to cancel.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Cancelling CTF event %s", event.id)

    try:
        with transaction.atomic():
            _transition_event(event, EventStatus.CANCELLED)
            _cancel_event_tasks(event)
    except CTFStateError:
        logger.warning(
            "Cannot cancel event %s: in terminal state %s",
            event.id,
            event.status,
        )
        return False

    # CTF-706: registered participants learn the event is off before their
    # ranges disappear; best-effort so a mail outage never blocks teardown.
    try:
        from ctf.services.notification import send_announcement

        send_announcement(
            event.pk,
            f"{event.name} has been cancelled",
            f"{event.name} has been cancelled by the organizer. "
            "All event ranges are being shut down and no further submissions are possible.",
            created_by=event.created_by,
        )
    except Exception:
        logger.exception("Failed to send cancellation notice for event %s", event.id)

    # Always destroy ranges on cancel — orphaned VMs waste money
    from ctf.services.range import cleanup_event_ranges

    result = cleanup_event_ranges(event.pk)
    logger.info("Range cleanup on event cancel %s: %s", event.id, result)

    logger.info("Cancelled CTF event %s", event.id)
    return True


def pause_event(event: CTFEvent) -> bool:
    """Pause an active event (transition to paused).

    Submissions are not accepted while paused.

    Args:
        event: The CTFEvent to pause.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Pausing CTF event %s", event.id)

    try:
        _transition_event(event, EventStatus.PAUSED)
    except CTFStateError:
        logger.warning(
            "Cannot pause event %s: not in active state (current: %s)",
            event.id,
            event.status,
        )
        return False

    logger.info("Paused CTF event %s", event.id)
    return True


def resume_event(event: CTFEvent) -> bool:
    """Resume a paused event (transition back to active).

    Args:
        event: The CTFEvent to resume.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Resuming CTF event %s", event.id)

    try:
        _transition_event(event, EventStatus.ACTIVE)
    except CTFStateError:
        logger.warning(
            "Cannot resume event %s: not in paused state (current: %s)",
            event.id,
            event.status,
        )
        return False

    logger.info("Resumed CTF event %s", event.id)
    return True


def archive_event(event: CTFEvent) -> bool:
    """Archive an ended event (transition to archived).

    Args:
        event: The CTFEvent to archive.

    Returns:
        True if transition succeeded, False otherwise.
    """
    logger.info("Archiving CTF event %s", event.id)

    try:
        _transition_event(event, EventStatus.ARCHIVED)
    except CTFStateError:
        logger.warning(
            "Cannot archive event %s: not in ended state (current: %s)",
            event.id,
            event.status,
        )
        return False

    logger.info("Archived CTF event %s", event.id)
    return True


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _transition_event(event: CTFEvent, target: EventStatus) -> None:
    """Perform a validated state transition.

    Args:
        event: The event to transition.
        target: The target status.

    Raises:
        CTFStateError: If the transition is invalid.
    """
    try:
        current = EventStatus(event.status)
    except ValueError:
        raise CTFStateError(
            f"Unknown event status: {event.status}",
            details={"event_id": str(event.id), "status": event.status},
        ) from None

    if not validate_transition(current, target):
        raise CTFStateError(
            f"Cannot transition from {current.value} to {target.value}",
            details={
                "event_id": str(event.id),
                "current_status": current.value,
                "target_status": target.value,
                "valid_targets": [s.value for s in VALID_TRANSITIONS.get(current, frozenset())],
            },
        )

    previous = current.value
    event.status = target.value
    event.save(update_fields=["status", "updated_at"])

    # CTF-1203: every validated state change fans out to registered webhooks.
    from ctf.services.webhook import emit_webhook

    emit_webhook(event, "event_state_change", {"from": previous, "to": target.value})
