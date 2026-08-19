"""CTF Event read-model queries: organizer listings and per-event statistics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q, QuerySet

from ctf.models import CTFEvent

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def get_organizer_events(
    user: User,
    *,
    status: str | None = None,
) -> QuerySet[CTFEvent]:
    """Get events an organizer administers, with an optional status filter.

    An organizer administers an event when they are its canonical owner
    (``created_by``) OR hold a live full co-organizer assignment on it (#1922).
    Moderator/judge assignments are bounded delegations, not organizer listings,
    so they are intentionally excluded here.

    Args:
        user: The organizer user.
        status: Optional status filter.

    Returns:
        Distinct QuerySet of CTFEvent instances the user administers.
    """
    from ctf.enums import EventStaffRole

    queryset = CTFEvent.objects.filter(
        Q(created_by=user)
        | Q(
            staff__user=user,
            staff__role=EventStaffRole.CO_ORGANIZER.value,
            staff__deleted_at__isnull=True,
        )
    ).distinct()

    if status:
        queryset = queryset.filter(status=status)

    return queryset.order_by("-event_start")


def get_event_stats(event: CTFEvent) -> dict[str, int]:
    """Get statistics for an event.

    Args:
        event: The event to get stats for.

    Returns:
        Dictionary with event statistics.
    """
    from django.db.models import Sum

    from ctf.enums import ParticipantStatus
    from ctf.models import CTFSubmission

    stats = {
        "participant_count": event.participants.count(),
        "registered_count": event.participants.filter(
            status__in=[
                ParticipantStatus.REGISTERED.value,
                ParticipantStatus.ACTIVE.value,
                ParticipantStatus.COMPLETED.value,
            ]
        ).count(),
        "challenge_count": event.challenges.count(),
        "team_count": event.teams.count() if event.team_mode else 0,
        "total_submissions": CTFSubmission.objects.filter(participant__event=event).count(),
        "correct_submissions": CTFSubmission.objects.filter(
            participant__event=event,
            is_correct=True,
        ).count(),
    }

    # Calculate total possible points
    points_result = event.challenges.aggregate(total=Sum("points"))
    stats["total_points"] = points_result["total"] or 0

    return stats
