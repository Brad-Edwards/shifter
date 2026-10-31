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
)
from ctf.services.communication.adapters import registered_channels

logger = logging.getLogger(__name__)


def _notice_identity(event_id: UUID, kind: str, occurrence: str, participant_id: UUID | None) -> UUID:
    """Return the stable campaign identity of one notice occurrence."""
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


def declare_participant_notice(
    participant: CTFParticipant, *, kind: str, subject: str, body: str, occurrence: str
) -> dict[str, str]:
    """Declare one participant notice in the caller's transaction (ADR-065-R5).

    The campaign, its scheduled intent and its release task commit with the
    change that caused the notice; the scheduler releases it and the worker
    sends it. The ``stage_notice`` identity makes a retry return the first
    declaration. A campaign from the older inline path has a manual trigger and
    is returned as ``existing`` without new work. The declaration skips the
    shared authoring budget; release admission still applies. A communication
    refusal rolls back only this savepoint and never fails the caller.
    """
    from ctf.services.communication.scheduling import schedule_declaration

    identity = _notice_identity(participant.event_id, kind, occurrence, participant.pk)
    try:
        with transaction.atomic():
            event = (
                CTFEvent.objects.select_for_update(of=("self",))
                .select_related("created_by")
                .get(pk=participant.event_id)
            )
            campaign = CommunicationCampaign.objects.filter(pk=identity).first()
            if campaign is None:
                campaign = create_system_event_campaign(
                    event,
                    CampaignDraft(
                        title=subject,
                        origin="system_milestone",
                        target_event_ids=[event.pk],
                        audience_spec={"kind": "participant", "participant_ids": [str(participant.pk)]},
                        trigger_spec={"kind": "absolute_time", "due_at": timezone.now().isoformat()},
                        channels=["email"],
                        subject=subject,
                        body=body,
                        notice_kind=kind,
                    ),
                    campaign_id=identity,
                    authoring_rate_limited=False,
                )
            result = {"campaign_id": str(campaign.pk)}
            if campaign.trigger_spec.get("kind") != "absolute_time":
                result["outcome"] = "existing"
            elif "email" not in registered_channels():
                result["outcome"] = "channel_unavailable"
            else:
                intent = schedule_declaration(
                    campaign,
                    due_at=datetime.fromisoformat(campaign.trigger_spec["due_at"]),
                    occurrence_key="notice",
                    actor=AdmissionActor(system=True),
                )
                result.update(intent_id=str(intent.pk), outcome="declared")
    except CTFCommunicationError as exc:
        logger.warning("Participant notice unavailable: participant=%s kind=%s code=%s", participant.pk, kind, exc.code)
        result = {"campaign_id": str(identity), "outcome": "unavailable"}
    return result


def participant_notice(participant_id: UUID, *, kind: str, subject: str, body: str) -> dict[str, str]:
    """Bind a lifecycle notice to its participant and range occurrence."""

    participant = CTFParticipant.objects.get(pk=participant_id)
    # Range generations distinguish later genuine notices; retries use the same key.
    return declare_participant_notice(
        participant,
        kind=kind,
        subject=subject,
        body=body,
        occurrence=str(participant.range_instance_id or "account"),
    )
