"""Server-owned participant-safe projections for the canonical CTF API.

These functions are the single place the participant ``/api/v1/ctf/me/*`` reads
are shaped. They compose the already-audited ``ctf.services.*`` facades and
return plain dicts that the DRF serializers in :mod:`ctf.api.serializers` type.

Permission-sensitive fields (flag hashes, flag formats, validator config,
solutions, unreleased/hidden content, other participants' scores) are filtered
here, server-side, so the participant surface can never leak them regardless of
what the client requests. Domain correctness (availability policy, prerequisite
gating, scoring) stays in the service layer; these helpers only marshal a
participant-safe view of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ctf.services.challenge import get_available_challenges
from ctf.services.submission import get_participant_submissions

if TYPE_CHECKING:
    from ctf.models import CTFParticipant


def participant_current_event(participant: CTFParticipant) -> dict[str, Any]:
    """Return the participant's current event plus their own participant state.

    The event fields are the read-only shape the participant workspace needs to
    render (scoring mode, team mode, scoreboard/attempt policy, window). No
    organizer-only configuration (range_config, reminder schedule, spare counts,
    email templates) is included.
    """
    event = participant.event
    return {
        "event": {
            "id": str(event.id),
            "name": event.name,
            "description": event.description,
            "status": event.status,
            "team_mode": event.team_mode,
            "scoring_mode": event.scoring_mode,
            "rating_visibility": event.rating_visibility,
            "attempt_limit_mode": event.attempt_limit_mode,
            "scoreboard_visible": event.scoreboard_visible,
            "event_start": event.event_start,
            "event_end": event.event_end,
        },
        "participant": _participant_self(participant),
    }


def _participant_self(participant: CTFParticipant) -> dict[str, Any]:
    """Return the participant's own state (never another participant's)."""
    team = participant.team
    bracket = participant.bracket
    return {
        "id": str(participant.id),
        "name": participant.name,
        "status": participant.status,
        "range_status": participant.range_status,
        "cached_score": participant.cached_score,
        "cached_solve_count": participant.cached_solve_count,
        "team": {"id": str(team.id), "name": team.name} if team is not None else None,
        "bracket": {"id": str(bracket.id), "name": bracket.name} if bracket is not None else None,
    }


def participant_challenge_list(participant: CTFParticipant) -> list[dict[str, Any]]:
    """Return the participant-safe browse list of available challenges.

    Uses the availability policy in :func:`ctf.services.challenge.get_available_challenges`
    (hidden/unreleased excluded, prerequisite-gated challenges excluded for this
    participant) and overlays this participant's own solve state. Flag hashes,
    flag formats, solutions, and validator config are never included.
    """
    challenges = get_available_challenges(participant.event_id, participant_id=participant.id)
    solved_ids = set(
        get_participant_submissions(participant.id).filter(is_correct=True).values_list("challenge_id", flat=True)
    )
    return [
        {
            "id": str(challenge.id),
            "name": challenge.name,
            "category": challenge.category,
            "points": challenge.points,
            "difficulty": challenge.difficulty,
            "order": challenge.order,
            "solved": challenge.id in solved_ids,
        }
        for challenge in challenges
    ]


def participant_team(participant: CTFParticipant) -> dict[str, Any] | None:
    """Return the participant's team and its members, or ``None`` when unteamed.

    Only teammate display names are exposed (no per-member scores or accounts),
    and only for the participant's own team. Returns ``None`` for solo events or
    an unassigned participant so the caller can render an empty state.
    """
    team = participant.team
    if team is None:
        return None
    members = team.members.filter(deleted_at__isnull=True).order_by("name")
    return {
        "id": str(team.id),
        "name": team.name,
        "members": [{"id": str(member.id), "name": member.name} for member in members],
    }
