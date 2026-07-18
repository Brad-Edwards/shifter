"""Challenge reads and participant availability/visibility policy."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from ctf.exceptions import CTFNotFoundError, CTFStateError, CTFValidationError
from ctf.models import (
    CTFChallenge,
    CTFChallengePrerequisite,
    CTFEvent,
    CTFParticipant,
)
from ctf.services.authorization import assert_actor_owns_event as _assert_actor_owns_event
from ctf.services.challenge.prerequisites import check_prerequisites_met

logger = logging.getLogger(__name__)


def _resolve_next_challenge(
    raw: Any,
    *,
    event: CTFEvent,
    self_id: UUID | None = None,
) -> CTFChallenge | None:
    """Resolve a `next_challenge` payload value into a CTFChallenge instance.

    Codex review (#765 cycle 6): an earlier change put `next_challenge` in
    the generic mutable-field allowlist, which let raw JSON UUIDs flow
    straight into `CTFChallenge.objects.create(...)` and crash with a 500
    on FK assignment, while internal callers passing a model instance
    bypassed self-reference and cross-event validation. Centralise the
    parse + validation here so every write path through
    `create_challenge` / `update_challenge` enforces the same rules.

    Accepts:
        - `None` / missing → no next challenge (return None)
        - `CTFChallenge` instance → validated and returned
        - UUID / str (UUID-shaped) → loaded and validated
        - anything else → `CTFValidationError`

    `self_id` is the id of the challenge being updated, so we can reject
    self-references. Cross-event references are also rejected.
    """
    if raw is None:
        return None

    if isinstance(raw, CTFChallenge):
        candidate = raw
    else:
        try:
            candidate_id = raw if isinstance(raw, UUID) else UUID(str(raw))
        except (ValueError, TypeError) as e:
            raise CTFValidationError(
                "next_challenge must be a UUID",
                details={"next_challenge": str(raw)},
            ) from e
        try:
            candidate = CTFChallenge.objects.get(pk=candidate_id)
        except CTFChallenge.DoesNotExist:
            raise CTFValidationError(
                f"next_challenge {candidate_id} not found",
                details={"next_challenge": str(candidate_id)},
            ) from None

    if self_id is not None and candidate.pk == self_id:
        raise CTFValidationError(
            "A challenge cannot be its own next_challenge",
            details={"challenge_id": str(self_id)},
        )
    if candidate.event_id != event.pk:
        raise CTFValidationError(
            "next_challenge must belong to the same event",
            details={
                "challenge_event": str(event.pk),
                "next_challenge_event": str(candidate.event_id),
            },
        )
    return candidate


def get_challenge(challenge_id: UUID) -> CTFChallenge:
    """Get a challenge by ID.

    Args:
        challenge_id: UUID of the challenge.

    Returns:
        The CTFChallenge instance.

    Raises:
        CTFNotFoundError: If challenge doesn't exist.
    """
    try:
        return CTFChallenge.objects.select_related("event").get(pk=challenge_id)
    except CTFChallenge.DoesNotExist:
        raise CTFNotFoundError(
            f"Challenge {challenge_id} not found",
            details={"challenge_id": str(challenge_id)},
        ) from None


def get_available_challenges(
    event_id: UUID,
    include_unreleased: bool = False,
    participant_id: UUID | None = None,
) -> QuerySet[CTFChallenge]:
    """Get challenges available for an event.

    Args:
        event_id: UUID of the event.
        include_unreleased: If True, include challenges with future release times.
        participant_id: If provided, exclude challenges with unmet prerequisites.

    Returns:
        QuerySet of CTFChallenge instances.
    """
    from django.db.models import Q

    qs = CTFChallenge.objects.filter(event_id=event_id)

    if not include_unreleased:
        now = timezone.now()
        # Exclude hidden challenges and those not yet released
        qs = qs.exclude(visibility="hidden")
        qs = qs.filter(Q(release_time__isnull=True) | Q(release_time__lte=now))

    if participant_id is not None:
        from ctf.models import CTFSubmission

        solved_ids = set(
            CTFSubmission.objects.filter(
                participant_id=participant_id,
                is_correct=True,
            ).values_list("challenge_id", flat=True)
        )
        # Exclude challenges that have active prerequisites not yet solved
        challenges_with_unmet = set()
        prereqs = CTFChallengePrerequisite.objects.filter(
            challenge__event_id=event_id,
        ).values_list("challenge_id", "required_challenge_id")
        for challenge_id, required_id in prereqs:
            if required_id not in solved_ids:
                challenges_with_unmet.add(challenge_id)

        if challenges_with_unmet:
            qs = qs.exclude(id__in=challenges_with_unmet)

    return qs.order_by("category", "order", "name")


def list_challenges_for_event(event_id: UUID, *, actor_id: int) -> QuerySet[CTFChallenge]:
    """List all challenges for an event (admin/organizer view).

    Args:
        event_id: UUID of the event.
        actor_id: User pk of the caller. Required (issue #765 DiD): the
            service refuses unless `actor_id == event.created_by_id`.

    Returns:
        QuerySet of CTFChallenge instances.

    Raises:
        CTFNotFoundError: If event doesn't exist.
        CTFPermissionError: If actor does not own the event.
    """
    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    _assert_actor_owns_event(actor_id, event)

    return CTFChallenge.objects.filter(event_id=event_id).order_by("category", "order", "name")


# Service-layer ownership helper lives in `ctf.services.authorization`
# (issue #765, codex review cycle 3). It is re-imported below so this
# module's existing internal callers (`_assert_actor_owns_event(...)`)
# keep working unchanged, while sibling services depend on the public
# helper directly instead of a private symbol in this module.

# -----------------------------------------------------------------------------
# Participant availability policy (issue #769)
# -----------------------------------------------------------------------------


def _assert_participant_eligible(participant: CTFParticipant) -> None:
    """Raise CTFStateError unless the participant is registered and non-disqualified."""
    from ctf.services.participant.queries import _PLAYING_PARTICIPANT_STATUSES

    # Participant eligibility: aligned with `eligible_participant_q`.
    if participant.registered_at is None or participant.status not in _PLAYING_PARTICIPANT_STATUSES:
        raise CTFStateError(
            "Participant is not eligible",
            details={
                "participant_id": str(participant.id),
                "status": participant.status,
            },
        )


def _assert_event_active_and_in_window(event: CTFEvent) -> None:
    """Raise CTFStateError unless the event is ACTIVE and within its competition window."""
    from ctf.enums import EventStatus

    if event.status != EventStatus.ACTIVE.value:
        raise CTFStateError(
            f"Event is not active (status: {event.status})",
            details={"event_id": str(event.id), "status": event.status},
        )

    now = timezone.now()
    if now < event.event_start or now > event.event_end:
        raise CTFStateError(
            "Event is not within its competition window",
            details={
                "event_id": str(event.id),
                "event_start": event.event_start.isoformat(),
                "event_end": event.event_end.isoformat(),
                "server_time": now.isoformat(),
            },
        )


def _assert_challenge_visible_and_released(challenge: CTFChallenge) -> None:
    """Raise CTFStateError unless the challenge is visible (not hidden/locked) and released."""
    if challenge.visibility == "hidden":
        raise CTFStateError(
            "Challenge is not available",
            details={"challenge_id": str(challenge.id)},
        )
    if challenge.visibility == "locked":
        raise CTFStateError(
            "Challenge is locked",
            details={"challenge_id": str(challenge.id)},
        )

    if not challenge.is_released:
        raise CTFStateError(
            "Challenge has not been released yet",
            details={
                "challenge_id": str(challenge.id),
                "release_time": challenge.release_time.isoformat() if challenge.release_time else None,
            },
        )


def assert_challenge_available_for_participant(
    participant: CTFParticipant,
    challenge: CTFChallenge,
) -> None:
    """Raise if `participant` cannot legitimately interact with `challenge`.

    Single source of truth for participant→challenge availability. Used by
    `submit_flag`, `use_hint`, `rate_challenge`, and the file-download
    endpoint so all paths apply the same checks; hints must not be cheaper
    to obtain than flag submission (issue #769).

    Checks, in order: (0) participant is registered & non-disqualified
    (codex review #765 cycle 6 — without this, an internal caller passing
    a raw participant_id for an INVITED or DISQUALIFIED row would bypass
    the eligibility check applied at the view layer), (1) challenge
    belongs to participant's event, (2) event is in ACTIVE status,
    (3) `now` is within `event_start..event_end`, (4) challenge visibility
    is not `hidden` or `locked`, (5) `challenge.is_released`,
    (6) prerequisites met.

    Raises:
        CTFValidationError: when challenge.event != participant.event.
        CTFStateError: any availability gate fails (including ineligible
            participant).
    """
    _assert_participant_eligible(participant)

    if challenge.event_id != participant.event_id:
        raise CTFValidationError(
            "Challenge does not belong to participant's event",
            details={
                "participant_event": str(participant.event_id),
                "challenge_event": str(challenge.event_id),
            },
        )

    _assert_event_active_and_in_window(challenge.event)
    _assert_challenge_visible_and_released(challenge)
    _assert_prerequisites_met(challenge, participant)


def assert_challenge_readable_for_participant(
    participant: CTFParticipant,
    challenge: CTFChallenge,
) -> None:
    """Raise if `participant` cannot READ this challenge's content.

    Codex review (#765 cycle 8): the submit/hint policy was too strict for
    the read-only detail page. `LOCKED` is documented as
    "shown-but-not-submittable", and the detail page also serves the
    `show_solution` view for ENDED/ARCHIVED events. Read-availability
    therefore omits the event-status, event-window, and locked-visibility
    gates that the write/unlock policy enforces.

    Read-availability still requires:
      - participant eligibility (registered, non-disqualified)
      - same-event match
      - challenge not HIDDEN
      - `challenge.is_released` (so future-release content stays hidden)
      - prerequisites met

    Used by participant-facing read endpoints (`challenge_detail`).
    Submit/hint/file-download endpoints continue to use
    `assert_challenge_available_for_participant`.
    """
    from ctf.services.participant.queries import _PLAYING_PARTICIPANT_STATUSES

    if participant.registered_at is None or participant.status not in _PLAYING_PARTICIPANT_STATUSES:
        raise CTFStateError(
            "Participant is not eligible",
            details={"participant_id": str(participant.id), "status": participant.status},
        )

    if challenge.event_id != participant.event_id:
        raise CTFValidationError(
            "Challenge does not belong to participant's event",
            details={
                "participant_event": str(participant.event_id),
                "challenge_event": str(challenge.event_id),
            },
        )

    if challenge.visibility == "hidden":
        raise CTFStateError(
            "Challenge is not available",
            details={"challenge_id": str(challenge.id)},
        )

    if not challenge.is_released:
        raise CTFStateError(
            "Challenge has not been released yet",
            details={
                "challenge_id": str(challenge.id),
                "release_time": challenge.release_time.isoformat() if challenge.release_time else None,
            },
        )

    _assert_prerequisites_met(challenge, participant)


def _assert_prerequisites_met(
    challenge: CTFChallenge,
    participant: CTFParticipant,
) -> None:
    """Raise CTFStateError if any of `challenge`'s prerequisites are unmet."""
    prereqs_met, unmet_challenges = check_prerequisites_met(challenge.id, participant.id)
    if not prereqs_met:
        unmet_names = [c.name for c in unmet_challenges]
        raise CTFStateError(
            f"Prerequisites not met. Complete first: {', '.join(unmet_names)}",
            details={
                "challenge_id": str(challenge.id),
                "unmet_prerequisites": [str(c.id) for c in unmet_challenges],
            },
        )


# -----------------------------------------------------------------------------
# Prerequisite Operations
# -----------------------------------------------------------------------------
