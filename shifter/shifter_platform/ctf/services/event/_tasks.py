"""CTF Event scheduled-task management: scheduling, rescheduling, cancellation.

Houses the internal helpers that create, reschedule, and cancel
``CTFScheduledTask`` rows for CTF events. Used by both the CRUD submodule
(rescheduling on time-window changes) and the lifecycle submodule
(scheduling on registration open, cancellation on delete/cancel) -- see the
package ``__init__`` docstring for the patch-locality rationale governing
those cross-submodule calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ctf.models import CTFEvent

logger = logging.getLogger(__name__)


def _schedule_event_tasks(event: CTFEvent) -> None:
    """Schedule automated tasks for an event.

    Tasks are recorded in the database and executed by the
    ``run_ctf_scheduler`` management command.
    """
    from datetime import timedelta

    from django.utils import timezone

    from ctf.enums import ScheduledTaskType
    from ctf.models import CTFScheduledTask

    now = timezone.now()

    # Spin up ranges before event
    CTFScheduledTask.objects.create(
        event=event,
        task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
        scheduled_for=event.get_spinup_time(),
    )

    # Event start
    CTFScheduledTask.objects.create(
        event=event,
        task_type=ScheduledTaskType.EVENT_START.value,
        scheduled_for=event.event_start,
    )

    # Event end
    CTFScheduledTask.objects.create(
        event=event,
        task_type=ScheduledTaskType.EVENT_END.value,
        scheduled_for=event.event_end,
    )

    # Schedule reminders at configurable intervals before event start
    reminder_intervals = [h for h in (event.reminder_hours or [24, 1]) if isinstance(h, int) and h > 0]
    for hours in reminder_intervals:
        reminder_time = event.event_start - timedelta(hours=hours)
        if reminder_time > now:
            CTFScheduledTask.objects.create(
                event=event,
                task_type=ScheduledTaskType.SEND_REMINDER.value,
                scheduled_for=reminder_time,
                metadata={"hours_before": hours},
            )

    logger.info("Scheduled tasks for event %s", event.id)


def _reschedule_live_event_schedule(event: CTFEvent) -> None:
    """Reschedule pending end-of-life tasks after event_end moves during a live event."""
    from ctf.enums import ScheduledTaskStatus, ScheduledTaskType
    from ctf.models import CTFScheduledTask

    for task in CTFScheduledTask.objects.filter(
        event=event,
        task_type=ScheduledTaskType.EVENT_END.value,
        status=ScheduledTaskStatus.PENDING.value,
    ):
        task.mark_cancelled()

    CTFScheduledTask.objects.create(
        event=event,
        task_type=ScheduledTaskType.EVENT_END.value,
        scheduled_for=event.event_end,
    )

    for task in CTFScheduledTask.objects.filter(
        event=event,
        task_type=ScheduledTaskType.CLEANUP_RANGES.value,
        status=ScheduledTaskStatus.PENDING.value,
    ):
        task.mark_cancelled()

    logger.info("Rescheduled live end tasks for event %s", event.id)


def _reschedule_event_tasks(event: CTFEvent) -> None:
    """Reschedule tasks after event times change."""
    _cancel_event_tasks(event)
    _schedule_event_tasks(event)
    _reschedule_challenge_release_tasks(event)
    logger.info("Rescheduled tasks for event %s", event.id)


def _reschedule_challenge_release_tasks(event: CTFEvent) -> None:
    """Recreate RELEASE_CHALLENGE tasks for all eligible challenges in the event."""
    from ctf.enums import ChallengeVisibility
    from ctf.models import CTFChallenge
    from ctf.services.challenge import _sync_release_task

    # CTFChallenge.objects is a SoftDeleteManager, so deleted rows are
    # already excluded by default — no inline deleted_at filter needed.
    challenges = CTFChallenge.objects.filter(
        event=event,
        visibility=ChallengeVisibility.HIDDEN.value,
        release_time__isnull=False,
    )
    for challenge in challenges:
        _sync_release_task(challenge)


def _cancel_event_tasks(event: CTFEvent) -> None:
    """Cancel all scheduled tasks for an event.

    Args:
        event: The CTFEvent to cancel tasks for.
    """
    from ctf.enums import ScheduledTaskStatus
    from ctf.models import CTFScheduledTask

    pending_tasks = CTFScheduledTask.objects.filter(
        event=event,
        status=ScheduledTaskStatus.PENDING.value,
    )

    cancelled_count = 0
    for task in pending_tasks:
        task.mark_cancelled()
        cancelled_count += 1

    if cancelled_count:
        logger.info("Cancelled %d scheduled tasks for event %s", cancelled_count, event.id)
