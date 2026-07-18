"""Challenge create/update/release/delete and payload shaping."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ctf.exceptions import CTFNotFoundError, CTFStateError, CTFValidationError
from ctf.models import (
    CTFChallenge,
    CTFChallengePrerequisite,
    CTFChallengeTag,
    CTFEvent,
    CTFTopic,
)
from ctf.services.authorization import assert_actor_owns_event as _assert_actor_owns_event
from ctf.services.challenge.access import _resolve_next_challenge
from ctf.services.challenge.flag_crud import _compute_legacy_flag_hash, add_flag
from ctf.services.challenge.flags import hash_flag
from shared.log_sanitize import safe_log_value

logger = logging.getLogger(__name__)


def _resolve_tags(event: CTFEvent, tag_names: list[str]) -> list[CTFChallengeTag]:
    """Get-or-create CTFChallengeTag objects for the given names within an event.

    Tag names are normalized to lowercase to prevent duplicates like "XDR" vs "xdr".
    """
    tags = []
    seen: set[str] = set()
    for name in tag_names:
        name = name.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        tag, _ = CTFChallengeTag.objects.get_or_create(
            event=event,
            name=name,
        )
        tags.append(tag)
    return tags


def _resolve_topics(topic_names: list[str]) -> list[CTFTopic]:
    """Get-or-create CTFTopic objects for the given names.

    Topic names are normalized to lowercase. Topics are global (not event-scoped).
    """
    topics = []
    seen: set[str] = set()
    for name in topic_names:
        name = name.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        topic, _ = CTFTopic.objects.get_or_create(name=name)
        topics.append(topic)
    return topics


# Fields that organizers may set when creating or updating challenges.
# All other fields (event, flag_hash, id, timestamps, etc.) are
# controlled internally and must not be overwritten by user input.
_CHALLENGE_MUTABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "category",
        "points",
        "difficulty",
        "flag_format",
        "solution",
        "max_attempts",
        "release_time",
        "order",
        "visibility",
        "target_instance_name",
        "target_port",
    }
)


def _reject_non_flag_live_edits(challenge: CTFChallenge, challenge_data: dict[str, Any]) -> None:
    """Refuse broad challenge edits during ACTIVE/PAUSED live events."""
    if challenge.event.is_content_modifiable:
        return
    if not challenge.event.is_live_flag_repairable:
        raise CTFStateError(
            f"Cannot modify challenge in event with status {challenge.event.status}",
            details={
                "challenge_id": str(challenge.pk),
                "event_status": challenge.event.status,
            },
        )
    allowed_keys = {"flag", "flags"}
    if not allowed_keys.intersection(challenge_data.keys()):
        raise CTFStateError(
            f"Only flag fields may be changed during a live event (status {challenge.event.status})",
            details={
                "challenge_id": str(challenge.pk),
                "event_status": challenge.event.status,
            },
        )
    extra_keys = set(challenge_data.keys()) - allowed_keys
    if extra_keys:
        raise CTFStateError(
            f"Only flag fields may be changed during a live event (status {challenge.event.status})",
            details={
                "challenge_id": str(challenge.pk),
                "event_status": challenge.event.status,
                "disallowed_fields": sorted(extra_keys),
            },
        )


def _apply_challenge_m2m(
    challenge: CTFChallenge,
    event: CTFEvent,
    *,
    tag_names: list[str] | None,
    topic_names: list[str] | None,
    flags_list: list[dict[str, Any]] | None,
    actor_id: int,
) -> None:
    """Apply tags, topics, and per-challenge flag records after a challenge
    has been created/updated. Caller is responsible for the surrounding
    `transaction.atomic()`.
    """
    if flags_list:
        for i, fd in enumerate(flags_list):
            add_flag(challenge.id, {**fd, "order": fd.get("order", i)}, actor_id=actor_id)
    if tag_names is not None:
        challenge.tags.set(_resolve_tags(event, tag_names))
    if topic_names is not None:
        challenge.topics.set(_resolve_topics(topic_names))


def _build_challenge_safe_data(data: dict[str, Any]) -> dict[str, Any]:
    """Filter `data` to allowed challenge fields, plus the explicitly
    handled `flag_hash` and pre-resolved `next_challenge` instance.
    `next_challenge` is kept out of the generic allowlist so unvalidated
    JSON FK input cannot crash FK assignment.
    """
    safe_data = {k: v for k, v in data.items() if k in _CHALLENGE_MUTABLE_FIELDS}
    if "flag_hash" in data:
        safe_data["flag_hash"] = data["flag_hash"]
    if "next_challenge" in data:
        safe_data["next_challenge"] = data["next_challenge"]
    return safe_data


def create_challenge(event_id: UUID, challenge_data: dict[str, Any], *, actor_id: int) -> CTFChallenge:
    """Create a new challenge.

    Args:
        event_id: UUID of the event to add the challenge to.
        challenge_data: Dictionary containing challenge fields.
            Must include 'flag' (plaintext) which will be hashed.
            May include 'flags' (list of dicts) for multi-flag challenges.
        actor_id: User pk of the caller. Required (issue #765 DiD): the
            service refuses unless `actor_id == event.created_by_id`, even
            when the view-layer ownership check has already passed. Pass
            `request.user.pk` from view callers.

    Returns:
        The created CTFChallenge instance.

    Raises:
        CTFNotFoundError: If event doesn't exist.
        CTFPermissionError: If actor does not own the event.
        CTFStateError: If event is not modifiable.
        CTFValidationError: If challenge data is invalid.
    """
    logger.info("Creating challenge for event %s", event_id)

    # Get and validate event
    try:
        event = CTFEvent.objects.get(pk=event_id)
    except CTFEvent.DoesNotExist:
        raise CTFNotFoundError(
            f"Event {event_id} not found",
            details={"event_id": str(event_id)},
        ) from None

    _assert_actor_owns_event(actor_id, event)

    if not event.is_content_modifiable:
        raise CTFStateError(
            f"Cannot add challenges to event in {event.status} state",
            details={"event_id": str(event_id), "status": event.status},
        )

    data = challenge_data.copy()
    flags_list = data.pop("flags", None)
    tag_names = data.pop("tags", None)
    topic_names = data.pop("topics", None)

    # Resolve next_challenge before allowlist filtering: codex review
    # cycle 6 — raw FK passthrough caused 500s and skipped same-event /
    # self-reference checks. Helper raises CTFValidationError on bad input.
    if "next_challenge" in data:
        data["next_challenge"] = _resolve_next_challenge(data["next_challenge"], event=event)

    # Validate: need either 'flag' or 'flags'
    if "flag" not in data and not flags_list:
        raise CTFValidationError(
            "Flag is required",
            details={"missing_fields": ["flag"]},
        )

    _compute_legacy_flag_hash(data, flags_list)
    safe_data = _build_challenge_safe_data(data)

    with transaction.atomic():
        challenge = CTFChallenge.objects.create(event=event, **safe_data)
        _apply_challenge_m2m(
            challenge,
            event,
            tag_names=tag_names,
            topic_names=topic_names,
            flags_list=flags_list,
            actor_id=actor_id,
        )

        logger.info(
            "Created challenge %s for event %s: %s",
            challenge.id,
            event_id,
            safe_log_value(challenge.name),
        )

    _sync_release_task(challenge)

    return challenge


def _build_safe_update_payload(data: dict[str, Any], challenge: CTFChallenge) -> dict[str, Any]:
    """Resolve `next_challenge`, hash any new `flag`, then filter to allowed fields.

    Mass-assignment safety: only `_CHALLENGE_MUTABLE_FIELDS` is allowed, plus
    explicit pass-throughs for `flag_hash` and `next_challenge` (which are kept
    out of the generic allowlist so JSON callers can't crash FK assignment).
    """
    if "next_challenge" in data:
        data["next_challenge"] = _resolve_next_challenge(
            data["next_challenge"],
            event=challenge.event,
            self_id=challenge.pk,
        )
    if "flag" in data:
        plaintext_flag = data.pop("flag")
        data["flag_hash"] = hash_flag(plaintext_flag)

    safe_data = {k: v for k, v in data.items() if k in _CHALLENGE_MUTABLE_FIELDS}
    if "flag_hash" in data:
        safe_data["flag_hash"] = data["flag_hash"]
    if "next_challenge" in data:
        safe_data["next_challenge"] = data["next_challenge"]
    return safe_data


def _apply_optional_challenge_associations(
    challenge: CTFChallenge,
    flags_list: list[dict[str, Any]] | None,
    tag_names: list[str] | None,
    topic_names: list[str] | None,
    actor_id: int,
) -> None:
    """Apply optional flag/tag/topic updates inside the existing transaction."""
    if flags_list is not None:
        challenge.flags.all().delete()
        for i, fd in enumerate(flags_list):
            add_flag(challenge.id, {**fd, "order": fd.get("order", i)}, actor_id=actor_id)
    if tag_names is not None:
        challenge.tags.set(_resolve_tags(challenge.event, tag_names))
    if topic_names is not None:
        challenge.topics.set(_resolve_topics(topic_names))


def update_challenge(challenge_id: UUID, challenge_data: dict[str, Any], *, actor_id: int) -> CTFChallenge:
    """Update an existing challenge.

    Args:
        challenge_id: UUID of the challenge to update.
        challenge_data: Dictionary containing fields to update.
            If 'flag' is provided, it will be re-hashed.
            If 'flags' is provided, all existing CTFFlag records are replaced.
        actor_id: User pk of the caller. Required (issue #765 DiD): the
            service refuses unless `actor_id == challenge.event.created_by_id`.

    Returns:
        The updated CTFChallenge instance.

    Raises:
        CTFNotFoundError: If challenge doesn't exist.
        CTFPermissionError: If actor does not own the event.
        CTFStateError: If challenge's event is not modifiable.
    """
    logger.info("Updating challenge %s", challenge_id)

    try:
        challenge = CTFChallenge.objects.select_related("event").get(pk=challenge_id)
    except CTFChallenge.DoesNotExist:
        raise CTFNotFoundError(
            f"Challenge {challenge_id} not found",
            details={"challenge_id": str(challenge_id)},
        ) from None

    _assert_actor_owns_event(actor_id, challenge.event)

    _reject_non_flag_live_edits(challenge, challenge_data)

    data = challenge_data.copy()
    flags_list = data.pop("flags", None)
    tag_names = data.pop("tags", None)
    topic_names = data.pop("topics", None)

    safe_data = _build_safe_update_payload(data, challenge)

    with transaction.atomic():
        for key, value in safe_data.items():
            setattr(challenge, key, value)
        challenge.save()
        _apply_optional_challenge_associations(challenge, flags_list, tag_names, topic_names, actor_id)
        logger.info("Updated challenge %s", safe_log_value(challenge_id))

        if challenge.event.is_live_flag_repairable and ("flag" in challenge_data or flags_list is not None):
            from ctf.services.audit import audit_live_flag_repair

            audit_live_flag_repair(
                actor_id=actor_id,
                challenge_id=challenge.pk,
                flag_id=challenge.flags.order_by("order").values_list("pk", flat=True).first() or challenge.pk,
                event_id=challenge.event_id,
                action="update_legacy_flag",
            )

    _sync_release_task(challenge)

    return challenge


def release_challenge(challenge_id: UUID) -> CTFChallenge:
    """Transition a challenge from HIDDEN to VISIBLE at its scheduled release time.

    Called by the scheduler when a RELEASE_CHALLENGE task fires.

    Args:
        challenge_id: UUID of the challenge to release.

    Returns:
        The updated CTFChallenge instance.

    Raises:
        CTFNotFoundError: If challenge doesn't exist.
    """
    from ctf.enums import ChallengeVisibility

    try:
        challenge = CTFChallenge.objects.select_related("event").get(pk=challenge_id)
    except CTFChallenge.DoesNotExist:
        raise CTFNotFoundError(
            f"Challenge {challenge_id} not found",
            details={"challenge_id": str(challenge_id)},
        ) from None

    if challenge.visibility != ChallengeVisibility.HIDDEN.value:
        logger.info(
            "Challenge %s is already %s, skipping release",
            challenge_id,
            challenge.visibility,
        )
        return challenge

    challenge.visibility = ChallengeVisibility.VISIBLE.value
    challenge.save(update_fields=["visibility", "updated_at"])
    logger.info("Released challenge %s: HIDDEN -> VISIBLE", challenge_id)
    return challenge


def _sync_release_task(challenge: CTFChallenge) -> None:
    """Create or cancel the RELEASE_CHALLENGE scheduled task for a challenge.

    Cancels any existing pending release task for the challenge, then creates
    a new one if the challenge is HIDDEN with a future release_time.
    """
    from ctf.enums import ChallengeVisibility, ScheduledTaskStatus, ScheduledTaskType
    from ctf.models import CTFScheduledTask

    # Cancel any existing pending release task for this challenge
    pending = CTFScheduledTask.objects.filter(
        event=challenge.event,
        task_type=ScheduledTaskType.RELEASE_CHALLENGE.value,
        status=ScheduledTaskStatus.PENDING.value,
        metadata__challenge_id=str(challenge.pk),
    )
    for task in pending:
        task.mark_cancelled()

    # Schedule a new release task if challenge is HIDDEN with a future release_time
    if (
        challenge.release_time is not None
        and challenge.visibility == ChallengeVisibility.HIDDEN.value
        and challenge.release_time > timezone.now()
    ):
        CTFScheduledTask.objects.create(
            event=challenge.event,
            task_type=ScheduledTaskType.RELEASE_CHALLENGE.value,
            scheduled_for=challenge.release_time,
            metadata={"challenge_id": str(challenge.pk)},
        )
        logger.info(
            "Scheduled release for challenge %s at %s",
            challenge.pk,
            challenge.release_time,
        )


def delete_challenge(challenge_id: UUID, *, actor_id: int) -> None:
    """Soft-delete a challenge.

    Args:
        challenge_id: UUID of the challenge to delete.
        actor_id: User pk of the caller. Required (issue #765 DiD): the
            service refuses unless `actor_id == challenge.event.created_by_id`.

    Raises:
        CTFNotFoundError: If challenge doesn't exist.
        CTFPermissionError: If actor does not own the event.
        CTFStateError: If challenge's event is not modifiable.
    """
    logger.info("Deleting challenge %s", challenge_id)

    try:
        challenge = CTFChallenge.objects.select_related("event").get(pk=challenge_id)
    except CTFChallenge.DoesNotExist:
        raise CTFNotFoundError(
            f"Challenge {challenge_id} not found",
            details={"challenge_id": str(challenge_id)},
        ) from None

    _assert_actor_owns_event(actor_id, challenge.event)

    if not challenge.event.is_content_modifiable:
        raise CTFStateError(
            f"Cannot delete challenge in event with status {challenge.event.status}",
            details={
                "challenge_id": str(challenge_id),
                "event_status": challenge.event.status,
            },
        )

    with transaction.atomic():
        # Soft-delete prerequisite links where this challenge is required
        CTFChallengePrerequisite.objects.filter(required_challenge=challenge).update(deleted_at=timezone.now())
        challenge.delete(soft=True)
    logger.info("Deleted challenge %s", challenge_id)
