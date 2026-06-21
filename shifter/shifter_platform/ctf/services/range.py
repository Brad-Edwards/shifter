"""CTF Range service.

Provides integration with Shifter's range infrastructure for CTF events.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ctf.enums import ScheduledTaskStatus, ScheduledTaskType
from ctf.exceptions import CTFNotFoundError, CTFRangeError
from ctf.models import CTFEvent, CTFParticipant, CTFScheduledTask
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# Throttled provisioning can sleep for up to 120s between participants and the
# retry backoff can wait even longer. The scheduler's liveness heartbeat file
# is checked at a 1-2 minute staleness threshold, so long waits are broken into
# small chunks that touch the heartbeat between them (and stay responsive to
# shutdown) rather than blocking in a single ``time.sleep`` call.
_HEARTBEAT_CHUNK_SECONDS = 15.0


def _interruptible_sleep(
    seconds: float,
    *,
    heartbeat: Callable[[], None] | None = None,
    shutdown_check: Callable[[], bool] | None = None,
) -> None:
    """Sleep ``seconds`` in <=``_HEARTBEAT_CHUNK_SECONDS`` increments.

    Touches ``heartbeat`` before each increment so a long wait does not let the
    scheduler liveness file go stale, and aborts early when ``shutdown_check``
    returns True so the caller stays responsive to SIGTERM.
    """
    remaining = float(seconds)
    while remaining > 0:
        if shutdown_check is not None and shutdown_check():
            return
        if heartbeat is not None:
            # A heartbeat failure during a wait must never abort provisioning;
            # the per-participant call site logs persistent failures (#942).
            with contextlib.suppress(Exception):
                heartbeat()
        chunk = min(_HEARTBEAT_CHUNK_SECONDS, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _is_already_assigned_error(exc: Exception) -> bool:
    """Return True for the benign 'already has a range' race-loser error (#942).

    A second (manual or scheduled) caller that finds a participant already
    assigned should be skipped, not counted as a provisioning failure.
    """
    return isinstance(exc, CTFRangeError) and "already has a range" in str(exc)


def provision_participant_range(participant_id: UUID) -> dict[str, Any]:
    """Provision a range for a participant.

    Uses the event's scenario_id to create a range via CMS.

    Args:
        participant_id: UUID of the participant.

    Returns:
        Dict with range instance ID and initial status.

    Raises:
        CTFNotFoundError: If participant doesn't exist.
        CTFRangeError: If range provisioning fails.
    """
    logger.info("Provisioning range for participant %s", safe_log_value(participant_id))

    # Lock the participant row for the whole assignment (#942 CTF-7): the
    # already-assigned check, CMS create call, instance-id lookup, and write must
    # be atomic so concurrent manual + scheduled provisioning cannot double-assign
    # a range. No select_related: `user` is nullable (FOR UPDATE rejects the
    # nullable side of an outer join on PostgreSQL) and joining `event` would
    # widen the lock to an event-wide row lock; both load lazily under the lock.
    with transaction.atomic():
        try:
            participant = CTFParticipant.objects.select_for_update().get(pk=participant_id)
        except CTFParticipant.DoesNotExist:
            raise CTFNotFoundError(
                f"Participant {participant_id} not found",
                details={"participant_id": str(participant_id)},
            ) from None

        if participant.user is None:
            raise CTFRangeError(
                "Participant must be registered before provisioning a range",
                details={"participant_id": str(participant_id)},
            )

        # An assignment is "claimed" the moment provisioning starts, not only
        # once a range_instance_id resolves (#942). cms_create_range can succeed
        # while cms_find_range_instance_id still returns None (the RangeInstance
        # row is not yet resolvable from the request id), in which case this
        # function persists range_status="provisioning" with a null
        # range_instance_id. Keying the guard solely on range_instance_id would
        # let the next caller re-provision that participant once the lock
        # releases, creating a second CMS range. Treat an in-progress
        # provisioning state as already-claimed so the benign race-loser path
        # skips it instead.
        if participant.range_instance_id or participant.range_status == "provisioning":
            raise CTFRangeError(
                "Participant already has a range assigned",
                details={
                    "participant_id": str(participant_id),
                    "range_instance_id": participant.range_instance_id,
                    "range_status": participant.range_status,
                },
            )

        event = participant.event
        agents_by_os = event.range_config.get("agents_by_os", {}) if event.range_config else {}
        ngfw_enabled = event.range_config.get("ngfw_enabled", False) if event.range_config else False

        try:
            from ctf.bridges import cms_create_range, cms_find_range_instance_id

            result = cms_create_range(
                user=participant.user,
                scenario=event.scenario_id,
                agents_by_os=agents_by_os,
                ngfw_enabled=ngfw_enabled,
            )
        except Exception as e:
            logger.exception("Range provisioning failed for participant %s", safe_log_value(participant_id))
            raise CTFRangeError(
                f"Range provisioning failed: {e}",
                details={"participant_id": str(participant_id)},
            ) from e

        # Store the RangeInstance reference
        range_instance_id = cms_find_range_instance_id(result.request_id)

        if range_instance_id:
            participant.range_instance_id = range_instance_id
        participant.range_status = "provisioning"
        participant.save(update_fields=["range_instance_id", "range_status", "updated_at"])

    return {
        "participant_id": str(participant_id),
        "range_instance_id": participant.range_instance_id,
        "status": "provisioning",
    }


def request_event_provisioning(event_id: UUID, *, source: str = "manual") -> CTFScheduledTask:
    """Enqueue (or coalesce onto) a due-now SPIN_UP_RANGES task for an event.

    The organizer "provision all" action runs the throttled provisioning loop
    on the CTF scheduler instead of the request thread. Repeated clicks and a
    pre-existing scheduled spin-up coalesce onto a single runnable task:

    - a RUNNING spin-up task is reused as-is (work is already in flight);
    - a PENDING spin-up task is reused; if it is scheduled in the future it is
      pulled forward to now so the manual action takes effect immediately and
      the original task cannot fire again later;
    - otherwise a new PENDING task due now is created.

    The work itself only provisions participants without a range, so resuming or
    re-running is idempotent.

    Args:
        event_id: UUID of the event.
        source: Audit hint recorded in task metadata (e.g. "manual").

    Returns:
        The active CTFScheduledTask (created or coalesced).

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    spin_up = ScheduledTaskType.SPIN_UP_RANGES.value
    with transaction.atomic():
        # Serialize concurrent enqueues for the same event so duplicate clicks
        # cannot each create a runnable task.
        try:
            CTFEvent.objects.select_for_update().get(pk=event_id)
        except CTFEvent.DoesNotExist:
            raise CTFNotFoundError(
                f"Event {event_id} not found",
                details={"event_id": str(event_id)},
            ) from None

        now = timezone.now()

        running = CTFScheduledTask.objects.filter(
            event_id=event_id,
            task_type=spin_up,
            status=ScheduledTaskStatus.RUNNING.value,
        ).first()
        if running is not None:
            logger.info("Coalescing provision request onto running task %s", running.pk)
            return running

        pending = (
            CTFScheduledTask.objects.filter(
                event_id=event_id,
                task_type=spin_up,
                status=ScheduledTaskStatus.PENDING.value,
            )
            .order_by("scheduled_for")
            .first()
        )
        if pending is not None:
            if pending.scheduled_for > now:
                pending.scheduled_for = now
                pending.metadata = {**(pending.metadata or {}), "source": source}
                pending.save(update_fields=["scheduled_for", "metadata", "updated_at"])
                logger.info(
                    "Pulled scheduled spin-up task %s forward for event %s", pending.pk, safe_log_value(event_id)
                )
            else:
                logger.info("Coalescing provision request onto pending task %s", pending.pk)
            return pending

        task = CTFScheduledTask.objects.create(
            event_id=event_id,
            task_type=spin_up,
            scheduled_for=now,
            metadata={"source": source},
        )
    logger.info("Enqueued provision task %s for event %s", task.pk, safe_log_value(event_id))
    return task


def get_provision_progress(event_id: UUID) -> dict[str, Any]:
    """Project provisioning progress for an event.

    Returns bounded aggregate participant counts (computed from the cached
    ``range_status``) plus the active spin-up task's status, suitable for
    polling from the organizer UI. Carries no raw error text.

    Args:
        event_id: UUID of the event.

    Returns:
        ``{"counts": {...}, "task": {...} | None}``.
    """
    counts = {
        "total": 0,
        "ready": 0,
        "provisioning": 0,
        "error": 0,
        "not_assigned": 0,
        "other": 0,
    }
    statuses = CTFParticipant.objects.filter(event_id=event_id).values_list("range_status", flat=True)
    for status in statuses:
        counts["total"] += 1
        key = status or "not_assigned"
        if key in counts and key != "total":
            counts[key] += 1
        else:
            counts["other"] += 1

    active = (
        CTFScheduledTask.objects.filter(
            event_id=event_id,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
            status__in=[ScheduledTaskStatus.PENDING.value, ScheduledTaskStatus.RUNNING.value],
        )
        .order_by("-scheduled_for")
        .first()
    )
    task_block = None
    if active is not None:
        task_block = {
            "id": str(active.pk),
            "status": active.status,
            "scheduled_for": active.scheduled_for.isoformat(),
        }

    return {"counts": counts, "task": task_block}


def _safe_heartbeat(heartbeat: Callable[[], None] | None, event_id: UUID) -> None:
    """Invoke the spin-up heartbeat, swallowing failures (#942).

    Keeps the claimed scheduled task fresh so the stale-recovery sweep does not
    mark this in-flight spin-up FAILED. A heartbeat error must never abort the
    spin-up.
    """
    if heartbeat is None:
        return
    try:
        heartbeat()
    except Exception:
        # A persistently failing heartbeat is what re-opens the stale-sweep bug
        # this guards against, so surface it (with traceback) rather than hide it.
        logger.exception("Spin-up heartbeat failed for event %s", event_id)


def _record_provision_attempt(
    participant: CTFParticipant,
    tallies: dict[str, int],
    errors: list[dict[str, str]],
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Provision one participant and fold the outcome into the running tallies.

    A benign 'already has a range' race loser is counted as ``skipped`` rather
    than ``failed`` so it does not poison the event spin-up (#942). The
    ``heartbeat`` is forwarded so the per-participant retry backoff also keeps
    the scheduled task and liveness file fresh (#943).
    """
    try:
        provision_participant_range_with_retry(participant.pk, heartbeat=heartbeat)
        tallies["successful"] += 1
    except Exception as e:
        if _is_already_assigned_error(e):
            tallies["skipped"] += 1
            logger.info("Participant %s already has a range; skipping", participant.pk)
        else:
            tallies["failed"] += 1
            errors.append({"participant_id": str(participant.pk), "error": str(e)})
            logger.exception("Failed to provision range for participant %s", participant.pk)


def provision_event_ranges_throttled(
    event_id: UUID,
    spinup_window_seconds: int,
    shutdown_check: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Provision ranges for all participants with throttled pacing.

    Spreads provisioning requests across ``spinup_window_seconds`` to avoid
    overwhelming AWS with simultaneous ECS tasks.

    Args:
        event_id: UUID of the event.
        spinup_window_seconds: Total window (seconds) over which to spread requests.
        shutdown_check: Optional callable returning True when the caller
            wants to abort (e.g. SIGTERM received by management command).
        heartbeat: Optional callable invoked between provisions and during the
            inter-provision waits so a long run keeps both its scheduled task
            fresh (so the stale-recovery sweep does not mark it FAILED on the
            multi-node portal, #942) and the scheduler liveness file fresh (so
            the container healthcheck does not restart it, #943). Failures are
            swallowed so a heartbeat error never aborts provisioning.

    Returns:
        Dict with counts of successful, failed, skipped, and whether interrupted.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    logger.info(
        "Throttled provisioning for event %s (window=%ds)",
        event_id,
        spinup_window_seconds,
    )

    try:
        CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    participants = list(
        CTFParticipant.objects.filter(
            event_id=event_id,
            range_instance_id__isnull=True,
        )
    )

    count = len(participants)
    if count == 0:
        return {
            "event_id": str(event_id),
            "total": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
            "interrupted": False,
        }

    # Delay between provisions, clamped to [5, 120] seconds
    raw_delay = spinup_window_seconds / max(count, 1)
    delay = max(5.0, min(120.0, raw_delay))

    tallies = {"successful": 0, "failed": 0, "skipped": 0}
    errors: list[dict[str, str]] = []
    interrupted = False

    for i, participant in enumerate(participants):
        if shutdown_check and shutdown_check():
            logger.info(
                "Throttled provisioning interrupted at %d/%d for event %s",
                i,
                count,
                event_id,
            )
            interrupted = True
            break

        _safe_heartbeat(heartbeat, event_id)
        _record_provision_attempt(participant, tallies, errors, heartbeat=heartbeat)

        logger.info(
            "Throttled provisioning progress for event %s: %d/%d (%d ready, %d failed, %d skipped)",
            event_id,
            i + 1,
            count,
            tallies["successful"],
            tallies["failed"],
            tallies["skipped"],
        )

        # Sleep between provisions (skip after the last one). The wait is
        # chunked so the heartbeat stays fresh and shutdown stays responsive.
        if i < count - 1 and not (shutdown_check and shutdown_check()):
            _interruptible_sleep(delay, heartbeat=heartbeat, shutdown_check=shutdown_check)

    # Notify organizer of failures
    if errors:
        from ctf.services.notification import notify_organizer_provision_failure

        notify_organizer_provision_failure(event_id, errors)

    return {
        "event_id": str(event_id),
        "total": tallies["successful"] + tallies["failed"] + tallies["skipped"],
        "successful": tallies["successful"],
        "failed": tallies["failed"],
        "skipped": tallies["skipped"],
        "errors": errors,
        "interrupted": interrupted,
    }


def get_range_status(participant_id: UUID) -> dict[str, Any]:
    """Get range status for a participant.

    Args:
        participant_id: UUID of the participant.

    Returns:
        Dict with range status information.

    Raises:
        CTFNotFoundError: If participant doesn't exist.
    """
    try:
        participant = CTFParticipant.objects.get(pk=participant_id)
    except CTFParticipant.DoesNotExist:
        raise CTFNotFoundError(
            f"Participant {participant_id} not found",
            details={"participant_id": str(participant_id)},
        ) from None

    if not participant.range_instance_id:
        return {
            "participant_id": str(participant_id),
            "status": "not_assigned",
            "range_instance_id": None,
        }

    # Query CMS for fresh status via bridge
    from ctf.bridges import cms_get_range_status

    fresh_status = cms_get_range_status(participant.range_instance_id)

    # Update cached status if changed
    if fresh_status != participant.range_status:
        participant.range_status = fresh_status
        participant.save(update_fields=["range_status", "updated_at"])

    return {
        "participant_id": str(participant_id),
        "status": participant.range_status,
        "range_instance_id": participant.range_instance_id,
    }


def _get_participant_with_range(participant_id: UUID) -> CTFParticipant:
    """Load participant, validate it has a range and a linked user."""
    try:
        participant = CTFParticipant.objects.select_related("user").get(pk=participant_id)
    except CTFParticipant.DoesNotExist:
        raise CTFNotFoundError(
            f"Participant {participant_id} not found",
            details={"participant_id": str(participant_id)},
        ) from None

    if not participant.range_instance_id:
        raise CTFRangeError(
            "No range assigned to participant",
            details={"participant_id": str(participant_id)},
        )

    if participant.user is None:
        raise CTFRangeError(
            "Participant has no linked user",
            details={"participant_id": str(participant_id)},
        )

    return participant


def stop_participant_range(participant_id: UUID) -> dict[str, Any]:
    """Stop (pause) a participant's range."""
    logger.info("Stopping range for participant %s", safe_log_value(participant_id))
    participant = _get_participant_with_range(participant_id)

    from ctf.bridges import cms_stop_range

    # guaranteed by _get_participant_with_range
    assert participant.range_instance_id is not None
    cms_stop_range(participant.user, participant.range_instance_id)
    participant.range_status = "stopping"
    participant.save(update_fields=["range_status", "updated_at"])
    return {"participant_id": str(participant_id), "status": "stopping"}


def start_participant_range(participant_id: UUID) -> dict[str, Any]:
    """Start (resume) a participant's stopped range."""
    logger.info("Starting range for participant %s", safe_log_value(participant_id))
    participant = _get_participant_with_range(participant_id)

    from ctf.bridges import cms_start_range

    # guaranteed by _get_participant_with_range
    assert participant.range_instance_id is not None
    cms_start_range(participant.user, participant.range_instance_id)
    participant.range_status = "resuming"
    participant.save(update_fields=["range_status", "updated_at"])
    return {"participant_id": str(participant_id), "status": "resuming"}


def restart_participant_range(participant_id: UUID) -> dict[str, Any]:
    """Restart a participant's range (stop then start)."""
    logger.info("Restarting range for participant %s", safe_log_value(participant_id))
    stop_participant_range(participant_id)
    return start_participant_range(participant_id)


def provision_participant_range_with_retry(
    participant_id: UUID,
    max_retries: int = 3,
    base_delay: int = 30,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Provision a range with exponential backoff retry.

    Args:
        participant_id: UUID of the participant.
        max_retries: Maximum retry attempts after initial failure.
        base_delay: Base delay in seconds between retries (doubled each attempt).
        heartbeat: Optional callable invoked during backoff waits so the
            scheduler liveness file stays fresh through long retries.

    Returns:
        Dict with range instance ID, status, and retry count.
    """
    last_error = None

    for attempt in range(1 + max_retries):
        try:
            result = provision_participant_range(participant_id)
            if attempt > 0:
                logger.info(
                    "Provisioning succeeded on attempt %d for participant %s",
                    attempt + 1,
                    participant_id,
                )
            result["retries"] = attempt
            return result
        except CTFRangeError as e:
            # Don't retry validation errors (no user, already assigned)
            if "must be registered" in str(e) or "already has a range" in str(e):
                raise
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                logger.warning(
                    "Provisioning attempt %d failed for participant %s, retrying in %ds: %s",
                    attempt + 1,
                    participant_id,
                    delay,
                    e,
                )
                _interruptible_sleep(delay, heartbeat=heartbeat)

    # All retries exhausted — mark as error
    try:
        participant = CTFParticipant.objects.get(pk=participant_id)
        participant.range_status = "error"
        participant.save(update_fields=["range_status", "updated_at"])
    except CTFParticipant.DoesNotExist:
        pass

    logger.error(
        "Provisioning failed after %d attempts for participant %s: %s",
        1 + max_retries,
        participant_id,
        last_error,
    )
    raise CTFRangeError(
        f"Provisioning failed after {1 + max_retries} attempts: {last_error}",
        details={"participant_id": str(participant_id), "retries": max_retries},
    ) from last_error


def cleanup_event_ranges(event_id: UUID) -> dict[str, Any]:
    """Cleanup (destroy) all ranges for an event.

    Args:
        event_id: UUID of the event.

    Returns:
        Dict with counts of destroyed and failed cleanups.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    logger.info("Cleaning up ranges for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    participants = CTFParticipant.objects.filter(
        event=event,
        range_instance_id__isnull=False,
    ).select_related("user")

    destroyed = 0
    failed = 0

    for participant in participants:
        try:
            _destroy_single_range(participant, participant.user)
            destroyed += 1
        except Exception as e:
            failed += 1
            logger.error(
                "Failed to destroy range for participant %s: %s",
                participant.pk,
                e,
            )

    return {
        "event_id": str(event_id),
        "total": destroyed + failed,
        "destroyed": destroyed,
        "failed": failed,
    }


def destroy_participant_range(participant_id: UUID) -> dict[str, Any]:
    """Destroy range for a single participant.

    Args:
        participant_id: UUID of the participant.

    Returns:
        Dict with destruction status.

    Raises:
        CTFNotFoundError: If participant doesn't exist.
        CTFRangeError: If no range assigned.
    """
    logger.info("Destroying range for participant %s", safe_log_value(participant_id))

    try:
        participant = CTFParticipant.objects.select_related("user").get(pk=participant_id)
    except CTFParticipant.DoesNotExist:
        raise CTFNotFoundError(
            f"Participant {participant_id} not found",
            details={"participant_id": str(participant_id)},
        ) from None

    if not participant.range_instance_id:
        raise CTFRangeError(
            "No range assigned to participant",
            details={"participant_id": str(participant_id)},
        )

    _destroy_single_range(participant, participant.user)

    return {
        "participant_id": str(participant_id),
        "status": "destroyed",
    }


def update_participant_range_status(participant_id: UUID) -> dict[str, Any]:
    """Poll CMS for fresh range status and update cached value.

    Args:
        participant_id: UUID of the participant.

    Returns:
        Dict with updated status.

    Raises:
        CTFNotFoundError: If participant doesn't exist.
    """
    return get_range_status(participant_id)


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _destroy_single_range(participant: CTFParticipant, user: User | None) -> None:
    """Destroy a single participant's range and clear fields."""
    from ctf.bridges import cms_destroy_range

    if participant.range_instance_id is None:
        logger.warning("No range_instance_id for participant %s, skipping destroy", participant.pk)
        return
    if user is None:
        logger.warning("No user for participant %s, skipping destroy", participant.pk)
        return
    cms_destroy_range(user, participant.range_instance_id)
    participant.range_instance_id = None
    participant.range_status = ""
    participant.save(update_fields=["range_instance_id", "range_status", "updated_at"])
