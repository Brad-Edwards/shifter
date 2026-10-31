"""Non-secret lifecycle notices submitted to the scoped ledger."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ctf.services.notification.ledger import declare_notice, participant_notice, stage_notice

if TYPE_CHECKING:
    from ctf.models import CTFEvent


def send_cleanup_warning(event_id: UUID) -> dict[str, str]:
    """Stage the non-secret notice for the scheduled cleanup occurrence."""

    from ctf.models import CTFEvent

    event = CTFEvent.objects.get(pk=event_id)
    return stage_notice(
        event_id,
        kind="cleanup",
        subject="Range cleanup approaching",
        body="Save your work before the scheduled range cleanup. Check the event page for its time.",
        occurrence=event.get_cleanup_time().isoformat(),
    )


def send_event_results(event_id: UUID) -> dict[str, str]:
    """Stage a notice that final event results are available."""

    return stage_notice(
        event_id,
        kind="results",
        subject="Event results available",
        body="Open the event scoreboard to view the final results.",
        occurrence="completed",
    )


def declare_cancellation_notice(event: CTFEvent) -> dict[str, str]:
    """Declare the participant cancellation notice inside the cancel transaction (CTF-706)."""

    return declare_notice(
        event,
        kind="cancellation",
        subject="Event cancelled",
        body="Submissions are closed. The event ranges are being removed.",
        occurrence="cancelled",
    )


def send_range_ready(participant_id: UUID) -> dict[str, str]:
    """Stage a participant notice for the current range generation."""

    return participant_notice(
        participant_id,
        kind="range_ready",
        subject="Your range is ready",
        body="Open your event range page to access your range.",
    )


def notify_participant_provision_failure(participant_id: UUID) -> dict[str, str]:
    """Stage a non-secret participant provisioning failure notice."""

    return participant_notice(
        participant_id,
        kind="range_failure",
        subject="Range provisioning needs attention",
        body="Open your event range page for the current status and contact your organizer.",
    )
