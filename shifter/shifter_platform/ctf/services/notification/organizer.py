"""Organizer-facing CTF notifications (provisioning failures, event start/end)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from ctf.enums import NotificationStatus, NotificationType
from ctf.models import CTFEvent, CTFNotification
from ctf.services.notification._email import _render_email, _send_email

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# SonarCloud S1192: extracted duplicated string literals.
NO_ORGANIZER_EMAIL_LOG = "Cannot notify: event %s has no organizer email"
EVENT_NOT_FOUND_LOG = "Cannot notify: event %s not found"


def notify_organizer_provision_failure(
    event_id: UUID,
    failures: list[dict[str, str]],
) -> None:
    """Notify the event organizer of provisioning failures.

    Args:
        event_id: UUID of the event.
        failures: List of dicts with participant_id and error.
    """
    if not failures:
        return

    logger.info("Notifying organizer of %d provisioning failures for event %s", len(failures), event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        logger.error(EVENT_NOT_FOUND_LOG, event_id)
        return

    organizer = event.created_by
    if not organizer or not organizer.email:
        logger.warning(NO_ORGANIZER_EMAIL_LOG, event_id)
        return

    html_content, text_content, custom_subject = _render_email(
        "provision_failure",
        {
            "event": event,
            "failures": failures,
            "failure_count": len(failures),
        },
        event=event,
    )

    _send_email(
        recipient=organizer.email,
        subject=custom_subject or f"Range provisioning failures: {event.name}",
        html_content=html_content,
        text_content=text_content,
    )

    # No synchronous delivery-success signal exists under async dispatch; the
    # record means "dispatched", not "delivered" (PLAT-103 clause 3).
    CTFNotification.objects.create(
        event=event,
        notification_type=NotificationType.PROVISION_FAILURE.value,
        subject=f"Provisioning failures for {event.name}",
        body=f"{len(failures)} participant(s) failed provisioning",
        status=NotificationStatus.SENT.value,
        recipient_filter="organizers",
        sent_count=1,
        created_by=organizer,
    )


def notify_organizer_event_start(event_id: UUID) -> None:
    """Notify the event organizer that the event has automatically started.

    Args:
        event_id: UUID of the event.
    """
    logger.info("Notifying organizer of event start for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        logger.error(EVENT_NOT_FOUND_LOG, event_id)
        return

    organizer = event.created_by
    if not organizer or not organizer.email:
        logger.warning(NO_ORGANIZER_EMAIL_LOG, event_id)
        return

    html_content, text_content, custom_subject = _render_email(
        "event_start",
        {"event": event},
        event=event,
    )

    _send_email(
        recipient=organizer.email,
        subject=custom_subject or f"Event started: {event.name}",
        html_content=html_content,
        text_content=text_content,
    )

    # No synchronous delivery-success signal exists under async dispatch; the
    # record means "dispatched", not "delivered" (PLAT-103 clause 3).
    CTFNotification.objects.create(
        event=event,
        notification_type=NotificationType.EVENT_START.value,
        subject=f"Event started: {event.name}",
        body=f"Event {event.name} has automatically started",
        status=NotificationStatus.SENT.value,
        recipient_filter="organizers",
        sent_count=1,
        created_by=organizer,
    )


def notify_organizer_event_end(event_id: UUID) -> None:
    """Notify the event organizer that the event has automatically ended.

    Args:
        event_id: UUID of the event.
    """
    logger.info("Notifying organizer of event end for event %s", event_id)

    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        logger.error(EVENT_NOT_FOUND_LOG, event_id)
        return

    organizer = event.created_by
    if not organizer or not organizer.email:
        logger.warning(NO_ORGANIZER_EMAIL_LOG, event_id)
        return

    html_content, text_content, custom_subject = _render_email(
        "event_end",
        {"event": event},
        event=event,
    )

    _send_email(
        recipient=organizer.email,
        subject=custom_subject or f"Event ended: {event.name}",
        html_content=html_content,
        text_content=text_content,
    )

    # No synchronous delivery-success signal exists under async dispatch; the
    # record means "dispatched", not "delivered" (PLAT-103 clause 3).
    CTFNotification.objects.create(
        event=event,
        notification_type=NotificationType.EVENT_END.value,
        subject=f"Event ended: {event.name}",
        body=f"Event {event.name} has automatically ended",
        status=NotificationStatus.SENT.value,
        recipient_filter="organizers",
        sent_count=1,
        created_by=organizer,
    )


# -----------------------------------------------------------------------------
# Private helpers
# -----------------------------------------------------------------------------
