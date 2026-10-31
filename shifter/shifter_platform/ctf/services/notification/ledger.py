"""Server-authored non-secret notices use the existing scoped ledger."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone

from ctf.exceptions import CTFCommunicationError
from ctf.models import CommunicationCampaign, CTFEvent, CTFParticipant
from ctf.services.communication import (
    AdmissionActor,
    CampaignDraft,
    create_system_event_campaign,
    release_campaign,
    schedule_declaration,
)
from ctf.services.communication.adapters import registered_channels

logger = logging.getLogger(__name__)


def _notice_identity(event_id: UUID, kind: str, occurrence: str, participant_id: UUID | None = None) -> UUID:
    """Return the stable campaign identity of one trusted notice occurrence."""
    return uuid5(NAMESPACE_URL, f"shifter:ctf-notice:{event_id}:{kind}:{occurrence}:{participant_id or ''}")


def stage_notice(
    event_id: UUID, *, kind: str, subject: str, body: str, occurrence: str, participant_id: UUID | None = None
) -> dict[str, str]:
    """One stable campaign per trusted occurrence; unavailable email is never dispatched.

    The original event owner remains the live author, with the same workspace and
    per-event checks as ordinary authoring. No missing actor becomes system trust.
    """
    with transaction.atomic():
        event = CTFEvent.objects.select_for_update(of=("self",)).select_related("created_by").get(pk=event_id)
        identity = _notice_identity(event.pk, kind, occurrence, participant_id)
        campaign = CommunicationCampaign.objects.filter(pk=identity).first()
        if campaign is None:
            audience = (
                {"kind": "event", "event_ids": [str(event.pk)]}
                if participant_id is None
                else {"kind": "participant", "participant_ids": [str(participant_id)]}
            )
            campaign = create_system_event_campaign(
                event,
                CampaignDraft(
                    title=subject,
                    origin="system_milestone",
                    target_event_ids=[event.pk],
                    audience_spec=audience,
                    trigger_spec={"kind": "manual"},
                    channels=["email"],
                    subject=subject,
                    body=body,
                    notice_kind=kind,
                ),
                campaign_id=identity,
            )
        if "email" not in registered_channels():
            return {"campaign_id": str(campaign.pk), "outcome": "channel_unavailable"}
        intent = release_campaign(campaign, occurrence_key="notice", admission=AdmissionActor(system=True))
        return {"campaign_id": str(campaign.pk), "intent_id": str(intent.pk), "outcome": "accepted"}


def declare_notice(event: CTFEvent, *, kind: str, subject: str, body: str, occurrence: str) -> dict[str, str]:
    """Declare one durable event notice inside the caller's transaction.

    The campaign, its scheduled intent and the release task commit with the
    caller's state change, so a crash after commit cannot lose the notice; the
    release task retries on backpressure. A refused declaration is logged with
    its bounded code and never fails the caller's state change.
    """
    identity = _notice_identity(event.pk, kind, occurrence)
    try:
        with transaction.atomic():
            locked = CTFEvent.objects.select_for_update(of=("self",)).select_related("created_by").get(pk=event.pk)
            campaign = CommunicationCampaign.objects.filter(pk=identity).first()
            if campaign is None:
                campaign = create_system_event_campaign(
                    locked,
                    CampaignDraft(
                        title=subject,
                        origin="system_milestone",
                        target_event_ids=[locked.pk],
                        audience_spec={"kind": "event", "event_ids": [str(locked.pk)]},
                        trigger_spec={"kind": "absolute_time", "due_at": timezone.now().isoformat()},
                        channels=["email"],
                        subject=subject,
                        body=body,
                        notice_kind=kind,
                    ),
                    campaign_id=identity,
                    authoring_rate_limited=False,
                )
            intent = schedule_declaration(
                campaign,
                due_at=datetime.fromisoformat(campaign.trigger_spec["due_at"]),
                occurrence_key="notice",
                actor=AdmissionActor(system=True),
            )
    except CTFCommunicationError as exc:
        logger.warning("Event notice unavailable: event=%s kind=%s code=%s", event.pk, kind, exc.code)
        return {"campaign_id": str(identity), "outcome": "unavailable"}
    return {"campaign_id": str(campaign.pk), "intent_id": str(intent.pk), "outcome": "declared"}


def participant_notice(participant_id: UUID, *, kind: str, subject: str, body: str) -> dict[str, str]:
    """Bind a lifecycle notice to its participant and range occurrence."""

    participant = CTFParticipant.objects.select_related("event").get(pk=participant_id)
    # Range generations distinguish later genuine notices; retries use the same key.
    return stage_notice(
        participant.event_id,
        kind=kind,
        subject=subject,
        body=body,
        occurrence=str(participant.range_instance_id or "account"),
        participant_id=participant.pk,
    )
