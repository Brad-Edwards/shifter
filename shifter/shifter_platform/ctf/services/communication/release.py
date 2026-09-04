"""Intent release for scoped communications (ADR-051, #2048).

Releasing a campaign resolves the audience server-side and, in ONE locked
transaction, writes the immutable intent, the deterministic per-recipient
snapshots and in-app receipts, the initial per-channel delivery commands, and a
strict audit event. PostgreSQL is authoritative; nothing calls a transport
before commit.

Release is linearized against cancellation and fencing: it takes the campaign and
target-event row locks, re-checks the campaign/event fences and resolves
recipients INSIDE the transaction, and its idempotency identity is scoped to the
immutable campaign so a replay can never return another campaign's intent. A
retry collapses onto the same intent, and per-recipient uniqueness means it can
never grow the audience (AC2, AC3).

The RAES/range-source reference columns on ``CommunicationIntent`` are populated
by the later RAES/range-ingress slices; this slice releases manual and
event-scoped campaigns and carries only the range-generation fence.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from ctf.communication_contracts import canonical_digest
from ctf.enums import CampaignStatus, DeliveryStatus, EventStatus, IntentStatus
from ctf.exceptions import CTFCommunicationError
from ctf.models import (
    CommunicationCampaign,
    CommunicationIntent,
    CTFEvent,
    CTFParticipant,
    DeliveryAttempt,
    MessageRevision,
    ParticipantReceipt,
    RecipientSnapshot,
)
from ctf.services.audit import audit_communication_release
from ctf.services.communication.audience import resolve_recipients

logger = logging.getLogger(__name__)


def _idempotency_key(campaign_id: UUID, occurrence_key: str, range_generation_ref: str) -> str:
    """Derive a campaign-scoped, unambiguous intent identity.

    The digest namespaces the caller-supplied occurrence and bound range
    generation with the immutable campaign id, using a structured (not
    delimiter-joined) encoding so different component tuples cannot collide onto
    one key or onto another campaign's intent.
    """
    return canonical_digest(
        {
            "campaign": str(campaign_id),
            "occurrence": occurrence_key,
            "generation": range_generation_ref,
        }
    )


def _assert_release_allowed(campaign: CommunicationCampaign, target_event_ids: set[UUID]) -> None:
    """Refuse release for a cancelled campaign or a cancelled target event."""
    if campaign.status == CampaignStatus.CANCELLED.value:
        raise CTFCommunicationError("A cancelled campaign cannot be released", code="CTF_COMMUNICATION_CANCELLED")
    cancelled = (
        CTFEvent.objects.select_for_update()
        .filter(id__in=target_event_ids, status=EventStatus.CANCELLED.value)
        .exists()
    )
    if cancelled:
        raise CTFCommunicationError(
            "A target event is cancelled; the campaign must be replaced",
            code="CTF_COMMUNICATION_EVENT_CANCELLED",
        )


def _resolve_revision(campaign: CommunicationCampaign, revision: MessageRevision | None) -> MessageRevision:
    """Return the revision to release, defaulting to the latest and validating ownership."""
    revision = revision or MessageRevision.objects.filter(campaign=campaign).order_by("-revision_number").first()
    if revision is None:
        raise CTFCommunicationError("Campaign has no message revision to release", code="CTF_COMMUNICATION_NO_CONTENT")
    if revision.campaign_id != campaign.pk:
        raise CTFCommunicationError(
            "Message revision does not belong to this campaign",
            code="CTF_COMMUNICATION_REVISION_MISMATCH",
        )
    return revision


def _materialize(
    intent: CommunicationIntent, recipients: list[CTFParticipant], channels: list[str], now: datetime
) -> None:
    """Write the per-recipient snapshots, receipts, and per-channel delivery commands."""
    for participant in recipients:
        snapshot = RecipientSnapshot.objects.create(
            intent=intent,
            event_id=participant.event_id,
            participant=participant,
            participant_public_id=participant.id,
            team_id=participant.team_id,
            user_id=participant.user_id,
            delivery_coordinate=participant.email or "",
        )
        ParticipantReceipt.objects.create(snapshot=snapshot)
        DeliveryAttempt.objects.bulk_create(
            [
                DeliveryAttempt(
                    intent=intent,
                    snapshot=snapshot,
                    channel=channel,
                    status=DeliveryStatus.QUEUED.value,
                    idempotency_key=f"{intent.idempotency_key}:{participant.id}:{channel}",
                    due_at=intent.due_at or now,
                )
                for channel in channels
            ]
        )


def release_campaign(
    campaign: CommunicationCampaign,
    *,
    occurrence_key: str,
    actor_user_id: int | None = None,
    actor_token_id: int | None = None,
    revision: MessageRevision | None = None,
    range_generation_ref: str = "",
) -> CommunicationIntent:
    """Release ``campaign`` as one immutable intent with materialized recipients.

    Returns the released intent. A second call with the same campaign, occurrence,
    and range generation returns the existing intent unchanged (at-least-once
    replay collapse), so the audience never grows on retry.
    """
    key = _idempotency_key(campaign.id, occurrence_key, range_generation_ref)
    now = timezone.now()

    with transaction.atomic():
        # Lock the campaign, then its target events, so a concurrent
        # cancellation/fencing transaction that also locks them cannot commit
        # between these checks and the inserts below (consistent lock order).
        locked = CommunicationCampaign.objects.select_for_update().get(pk=campaign.pk)
        existing = CommunicationIntent.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.campaign_id != locked.pk:
                raise CTFCommunicationError(
                    "Idempotency identity does not match this campaign",
                    code="CTF_COMMUNICATION_IDEMPOTENCY_MISMATCH",
                )
            return existing

        target_event_ids = set(locked.target_events.values_list("id", flat=True))
        _assert_release_allowed(locked, target_event_ids)
        revision = _resolve_revision(locked, revision)
        recipients = resolve_recipients(target_event_ids, locked.audience_spec)
        channels = list(locked.channels)

        try:
            intent = CommunicationIntent.objects.create(
                campaign=locked,
                revision=revision,
                status=IntentStatus.RELEASED.value,
                trigger_kind=locked.trigger_spec["kind"],
                origin=locked.origin,
                actor_user_id=actor_user_id if actor_user_id is not None else locked.created_by_id,
                actor_token_id=actor_token_id if actor_token_id is not None else locked.actor_token_id,
                channels=channels,
                acknowledgement_policy=locked.acknowledgement_policy,
                occurrence_key=occurrence_key,
                idempotency_key=key,
                range_generation_ref=range_generation_ref,
                released_at=now,
            )
        except IntegrityError:
            committed = CommunicationIntent.objects.filter(idempotency_key=key, campaign=locked).first()
            if committed is not None:
                return committed
            raise

        _materialize(intent, recipients, channels, now)
        CommunicationCampaign.objects.filter(pk=locked.pk).update(status=CampaignStatus.RELEASED.value, updated_at=now)
        audit_communication_release(
            actor_id=intent.actor_user_id,
            campaign_id=locked.id,
            intent_id=intent.id,
            workspace_id=locked.workspace_id,
            recipient_count=len(recipients),
            channels=channels,
        )
    logger.info(
        "Released communication intent %s for campaign %s (%d recipients)",
        intent.id,
        campaign.id,
        len(recipients),
    )
    return intent
