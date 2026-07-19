"""Participant-facing CTF notification delivery (invitations, credentials, reminders, announcements)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ctf.enums import NotificationStatus, NotificationType, ScheduledTaskType
from ctf.exceptions import CTFNotFoundError
from ctf.models import CTFEvent, CTFNotification, CTFParticipant
from ctf.services.notification._email import _render_email, _send_email
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def send_invitations(event_id: UUID) -> dict[str, Any]:
    """Reset and queue credentials for participants with delivery email.

    Args:
        event_id: UUID of the event.

    Returns:
        Dict with sent count and any errors.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    logger.info("Sending invitations for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    participants = CTFParticipant.objects.filter(event=event)

    queued = 0
    failed = 0

    for participant in participants:
        if not participant.email:
            continue
        try:
            from ctf.services.participant.accounts import reset_participant_credentials

            reset_participant_credentials(participant.pk)

            from django.utils import timezone

            participant.invited_at = timezone.now()
            participant.save(update_fields=["invited_at", "updated_at"])
            queued += 1
        except Exception:
            logger.exception("Failed to send invitation to %s", safe_log_value(participant.email))
            failed += 1

    # Create notification record
    if queued > 0:
        CTFNotification.objects.create(
            event=event,
            notification_type=NotificationType.INVITE.value,
            subject=f"Invitations for {event.name}",
            body=f"Queued {queued} invitations",
            status=NotificationStatus.SENT.value,
            recipient_filter="participants",
            sent_count=queued,
            created_by=event.created_by,
        )

    return {
        "event_id": str(event_id),
        "total": queued + failed,
        "sent": queued,
        "failed": failed,
    }


def send_credentials(event_id: UUID) -> dict[str, Any]:
    """Send credential emails to participants with ready ranges.

    Args:
        event_id: UUID of the event.

    Returns:
        Dict with sent count and any errors.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    logger.info("Sending credentials for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    participants = CTFParticipant.objects.filter(
        event=event,
        range_status="ready",
    )

    queued = 0
    failed = 0

    for participant in participants:
        try:
            # Link to the CTF range page where participants can access their range
            # via the platform's standard Guacamole RDP flow.
            from django.conf import settings
            from django.urls import reverse

            range_page_url = reverse("ctf:participant_range")
            base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
            access_url = f"{base}{range_page_url}"

            html_content, text_content, custom_subject = _render_email(
                "credentials",
                {
                    "event": event,
                    "participant": participant,
                    "access_url": access_url,
                },
                event=event,
            )
            _send_email(
                recipient=participant.email,
                subject=custom_subject or f"Your credentials for {event.name}",
                html_content=html_content,
                text_content=text_content,
            )
            queued += 1
        except Exception:
            logger.exception("Failed to send credentials to %s", safe_log_value(participant.email))
            failed += 1

    if queued > 0:
        CTFNotification.objects.create(
            event=event,
            notification_type=NotificationType.CREDENTIALS.value,
            subject=f"Credentials for {event.name}",
            body=f"Queued credentials for {queued} participants",
            status=NotificationStatus.SENT.value,
            recipient_filter="participants",
            sent_count=queued,
            created_by=event.created_by,
        )

    return {
        "event_id": str(event_id),
        "total": queued + failed,
        "sent": queued,
        "failed": failed,
    }


def send_reminder(event_id: UUID, hours_before: int = 24) -> dict[str, Any]:
    """Send reminder emails to registered participants.

    Args:
        event_id: UUID of the event.
        hours_before: Hours before event this reminder is for.

    Returns:
        Dict with sent count and any errors.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    logger.info("Sending %d-hour reminder for event %s", hours_before, event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    participants = CTFParticipant.objects.filter(
        event=event,
        registered_at__isnull=False,
    )

    queued = 0
    failed = 0

    # Build access URL and timezone-aware start time for template context
    from zoneinfo import ZoneInfo

    from django.conf import settings
    from django.urls import reverse

    event_page_url = reverse("ctf:participant_event")
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    access_url = f"{base}{event_page_url}"

    tz_name = event.event_timezone or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    event_start_local = event.event_start.astimezone(tz)

    for participant in participants:
        try:
            html_content, text_content, custom_subject = _render_email(
                "reminder",
                {
                    "event": event,
                    "participant": participant,
                    "hours_before": hours_before,
                    "access_url": access_url,
                    "event_start_local": event_start_local,
                    "event_timezone": tz_name,
                },
                event=event,
            )
            _send_email(
                recipient=participant.email,
                subject=custom_subject or f"Reminder: {event.name} starts soon",
                html_content=html_content,
                text_content=text_content,
            )
            queued += 1
        except Exception:
            logger.exception("Failed to send reminder to %s", safe_log_value(participant.email))
            failed += 1

    if queued > 0:
        CTFNotification.objects.create(
            event=event,
            notification_type=NotificationType.REMINDER.value,
            subject=f"Reminder for {event.name}",
            body=f"Queued {queued} reminders",
            status=NotificationStatus.SENT.value,
            recipient_filter="participants",
            sent_count=queued,
            created_by=event.created_by,
        )

    return {
        "event_id": str(event_id),
        "hours_before": hours_before,
        "total": queued + failed,
        "sent": queued,
        "failed": failed,
    }


def send_cleanup_warning(event_id: UUID) -> dict[str, Any]:
    """Warn registered participants that range destruction is imminent (CTF-1003).

    Delivered through the announcement pipeline shortly before the scheduled
    ``CLEANUP_RANGES`` task fires, so participants can save work off their
    ranges. Returns sent/failed counts like :func:`send_reminder`.
    """
    logger.info("Sending cleanup warning for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    from zoneinfo import ZoneInfo

    tz_name = event.event_timezone or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    cleanup_local = event.get_cleanup_time().astimezone(tz)
    subject = f"{event.name}: ranges will be destroyed soon"
    body = (
        f"The ranges for {event.name} are scheduled for destruction at "
        f"{cleanup_local.strftime('%Y-%m-%d %H:%M')} {tz_name}. "
        "Save anything you need off your range before then; this cannot be undone."
    )

    participants = CTFParticipant.objects.filter(event=event, registered_at__isnull=False).exclude(email="")
    sent = 0
    failed = 0
    for participant in participants:
        try:
            html_content, text_content, custom_subject = _render_email(
                "announcement",
                {
                    "event": event,
                    "participant": participant,
                    "subject": subject,
                    "body": body,
                },
                event=event,
            )
            _send_email(
                recipient=participant.email,
                subject=custom_subject or subject,
                html_content=html_content,
                text_content=text_content,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Failed to send cleanup warning to %s", safe_log_value(participant.email))

    return {"sent": sent, "failed": failed}


def send_announcement(
    event_id: UUID,
    subject: str,
    body: str,
    created_by: User,
) -> CTFNotification:
    """Send an announcement to all participants.

    Args:
        event_id: UUID of the event.
        subject: Email subject.
        body: Email body content.
        created_by: User creating the announcement.

    Returns:
        The CTFNotification record.

    Raises:
        CTFNotFoundError: If event doesn't exist.
    """
    # Do not log the user-controlled announcement subject (SonarCloud S5145 /
    # log-injection): the event id is sufficient to trace the operation.
    logger.info("Sending announcement for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    notification = CTFNotification.objects.create(
        event=event,
        notification_type=NotificationType.ANNOUNCEMENT.value,
        subject=subject,
        body=body,
        status=NotificationStatus.SENDING.value,
        recipient_filter="participants",
        created_by=created_by,
    )
    _deliver_announcement(notification)
    return notification


def _deliver_announcement(notification: CTFNotification) -> int:
    """Email an announcement row to every participant and mark it sent.

    Shared by the immediate path (:func:`send_announcement`) and the
    scheduler's SEND_NOTIFICATION handler, so scheduled announcements
    deliver the drafted content (#667). Also mirrors the announcement onto
    the real-time bus (CTF-802/CTF-803).
    """
    event = notification.event
    subject = notification.subject
    body = notification.body
    participants = CTFParticipant.objects.filter(event=event).exclude(email="")
    queued = 0

    for participant in participants:
        try:
            html_content, text_content, custom_subject = _render_email(
                "announcement",
                {
                    "event": event,
                    "participant": participant,
                    "subject": subject,
                    "body": body,
                },
                event=event,
            )
            _send_email(
                recipient=participant.email,
                subject=custom_subject or subject,
                html_content=html_content,
                text_content=text_content,
            )
            queued += 1
        except Exception:
            logger.exception("Failed to send announcement to %s", safe_log_value(participant.email))

    from django.utils import timezone

    notification.sent_count = queued
    notification.sent_at = timezone.now()
    notification.status = NotificationStatus.SENT.value
    notification.save(update_fields=["sent_count", "sent_at", "status", "updated_at"])

    from ctf.services.notification.realtime import publish_event_notification

    publish_event_notification(
        event,
        "announcement",
        {"subject": subject, "body": body, "notification_id": str(notification.pk)},
    )
    return queued


def schedule_notification(
    notification_id: UUID,
    scheduled_at: datetime,
) -> CTFNotification:
    """Schedule a notification for future sending.

    Args:
        notification_id: UUID of the notification.
        scheduled_at: When to send the notification.

    Returns:
        The updated CTFNotification record.

    Raises:
        CTFNotFoundError: If notification doesn't exist.
    """
    try:
        notification = CTFNotification.objects.get(pk=notification_id)
    except CTFNotification.DoesNotExist:
        raise CTFNotFoundError(
            f"Notification {notification_id} not found",
            details={"notification_id": str(notification_id)},
        ) from None

    notification.scheduled_at = scheduled_at
    notification.status = NotificationStatus.SCHEDULED.value
    notification.save(update_fields=["scheduled_at", "status", "updated_at"])

    # Create scheduled task
    from ctf.models import CTFScheduledTask

    CTFScheduledTask.objects.create(
        event=notification.event,
        task_type=ScheduledTaskType.SEND_NOTIFICATION.value,
        scheduled_for=scheduled_at,
        metadata={"notification_id": str(notification_id)},
    )

    return notification


def cancel_scheduled_notification(notification_id: UUID) -> CTFNotification:
    """Cancel a scheduled notification before delivery (CTF-804).

    Reverts the row to draft and cancels its pending scheduler task.
    """
    from ctf.enums import ScheduledTaskStatus
    from ctf.exceptions import CTFStateError
    from ctf.models import CTFScheduledTask

    try:
        notification = CTFNotification.objects.get(pk=notification_id)
    except CTFNotification.DoesNotExist:
        raise CTFNotFoundError(
            f"Notification {notification_id} not found",
            details={"notification_id": str(notification_id)},
        ) from None
    if notification.status != NotificationStatus.SCHEDULED.value:
        raise CTFStateError(
            "Only scheduled notifications can be cancelled",
            details={"notification_id": str(notification_id), "status": notification.status},
        )
    notification.status = NotificationStatus.DRAFT.value
    notification.scheduled_at = None
    notification.save(update_fields=["status", "scheduled_at", "updated_at"])
    for task in CTFScheduledTask.objects.filter(
        event=notification.event,
        task_type=ScheduledTaskType.SEND_NOTIFICATION.value,
        status=ScheduledTaskStatus.PENDING.value,
        metadata__notification_id=str(notification_id),
    ):
        task.mark_cancelled()
    logger.info("Cancelled scheduled notification %s", notification_id)
    return notification


def deliver_scheduled_notification(notification_id: UUID) -> int:
    """Deliver a scheduled notification's drafted content (#667 scheduler path)."""
    from ctf.exceptions import CTFStateError

    try:
        notification = CTFNotification.objects.select_related("event").get(pk=notification_id)
    except CTFNotification.DoesNotExist:
        raise CTFNotFoundError(
            f"Notification {notification_id} not found",
            details={"notification_id": str(notification_id)},
        ) from None
    if notification.status != NotificationStatus.SCHEDULED.value:
        raise CTFStateError(
            "Notification is not scheduled",
            details={"notification_id": str(notification_id), "status": notification.status},
        )
    notification.status = NotificationStatus.SENDING.value
    notification.save(update_fields=["status", "updated_at"])
    return _deliver_announcement(notification)


def send_event_results(event_id: UUID) -> dict[str, Any]:
    """Email final results to registered participants at event completion (CTF-801).

    Each recipient gets their own final rank/score/solves plus a short
    top-of-board summary. Best-effort per recipient; hidden and observer
    participants receive the summary without a rank.
    """
    logger.info("Sending event results for event %s", event_id)
    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    from ctf.services.scoring import get_scoreboard

    rows = get_scoreboard(event.pk)
    rank_by_participant = {row["participant_id"]: index + 1 for index, row in enumerate(rows)}
    top_summary = "; ".join(f"{index + 1}. {row['name']} ({row['score']})" for index, row in enumerate(rows[:5]))

    participants = CTFParticipant.objects.filter(event=event, registered_at__isnull=False).exclude(email="")
    sent = 0
    failed = 0
    for participant in participants:
        rank = rank_by_participant.get(str(participant.pk))
        try:
            html_content, text_content, custom_subject = _render_email(
                "event_results",
                {
                    "event": event,
                    "participant": participant,
                    "final_rank": rank if rank is not None else "—",
                    "final_score": participant.cached_score,
                    "solve_count": participant.cached_solve_count,
                    "top_summary": top_summary,
                },
                event=event,
            )
            _send_email(
                recipient=participant.email,
                subject=custom_subject or f"{event.name}: final results",
                html_content=html_content,
                text_content=text_content,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Failed to send results to %s", safe_log_value(participant.email))

    _record_notification(
        event,
        NotificationType.EVENT_RESULTS.value,
        f"{event.name}: final results",
        sent,
    )
    return {"sent": sent, "failed": failed}


def send_range_ready(participant_id: UUID) -> bool:
    """Tell one participant their range is ready (CTF-801), best-effort."""
    participant = (
        CTFParticipant.objects.select_related("event").filter(pk=participant_id, deleted_at__isnull=True).first()
    )
    if participant is None or not participant.email:
        return False
    event = participant.event
    try:
        from django.conf import settings
        from django.urls import reverse

        base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
        access_url = f"{base}{reverse('ctf:participant_range')}"
        html_content, text_content, custom_subject = _render_email(
            "range_ready",
            {"event": event, "participant": participant, "access_url": access_url},
            event=event,
        )
        _send_email(
            recipient=participant.email,
            subject=custom_subject or f"{event.name}: your range is ready",
            html_content=html_content,
            text_content=text_content,
        )
    except Exception:
        logger.exception("Failed to send range-ready notice to %s", safe_log_value(participant.email))
        return False
    return True


def notify_participant_provision_failure(participant_id: UUID) -> bool:
    """Tell one participant their range hit a provisioning problem (CTF-801)."""
    participant = (
        CTFParticipant.objects.select_related("event").filter(pk=participant_id, deleted_at__isnull=True).first()
    )
    if participant is None or not participant.email:
        return False
    event = participant.event
    try:
        html_content, text_content, custom_subject = _render_email(
            "provision_failure_participant",
            {"event": event, "participant": participant},
            event=event,
        )
        _send_email(
            recipient=participant.email,
            subject=custom_subject or f"{event.name}: a problem with your range",
            html_content=html_content,
            text_content=text_content,
        )
    except Exception:
        logger.exception("Failed to send provision-failure notice to %s", safe_log_value(participant.email))
        return False
    return True


def _record_notification(event: CTFEvent, notification_type: str, subject: str, sent_count: int) -> None:
    """Write the audit row for an automatic notification burst."""
    from django.utils import timezone

    CTFNotification.objects.create(
        event=event,
        notification_type=notification_type,
        subject=subject,
        body=subject,
        status=NotificationStatus.SENT.value,
        recipient_filter="participants",
        sent_count=sent_count,
        sent_at=timezone.now(),
        created_by=event.created_by,
    )
