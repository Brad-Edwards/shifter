"""Participant lifecycle notices on the communication ledger (ADR-065-R5, #664).

Drives the real seat services, the real declaration, the scheduled release task,
the worker and the email adapter. Only the mail transport is Django's locmem
backend.
"""

from __future__ import annotations

import logging

import pytest
from django.db import transaction

from ctf.enums import ScheduledTaskType
from ctf.models import CommunicationCampaign, CommunicationIntent, CTFParticipant, CTFScheduledTask
from ctf.services import notification
from ctf.services.communication import delivery as delivery_svc
from ctf.services.participant.accounts import create_participant_accounts
from ctf.services.participant.bulk_import import bulk_import_participants
from ctf.services.participant.lifecycle import add_participant

pytestmark = pytest.mark.django_db

SITE_URL = "https://ctf.example.test"


@pytest.fixture(autouse=True)
def _email(settings, monkeypatch):
    settings.EMAIL_DELIVERY_AVAILABLE = True
    settings.SITE_URL = SITE_URL
    monkeypatch.setattr("ctf.services.communication.metrics._resolve_client", lambda: None)


def _registration_intents(event):
    return CommunicationIntent.objects.filter(campaign__notice_kind="registration", campaign__target_events=event)


def _release(intent_id):
    from ctf.services.communication.scheduling import run_release_communication_task

    task = CTFScheduledTask.objects.get(
        task_type=ScheduledTaskType.RELEASE_COMMUNICATION.value, metadata__intent_id=str(intent_id)
    )
    return run_release_communication_task(task)


def _ready_participant(event, name="racer", range_id=42):
    return CTFParticipant.objects.create(
        event=event,
        email=f"{name}@test.com",
        name=name,
        status="active",
        range_instance_id=range_id,
        range_status="provisioning",
    )


def test_every_seat_path_declares_one_registration_confirmation(ctf_event, organizer_credential):
    add_participant(ctf_event.pk, "single@test.com", "Single", actor=organizer_credential)
    bulk_import_participants(ctf_event.pk, "name,email\nAda,ada@test.com\nNo Mail,\n", actor=organizer_credential)
    create_participant_accounts(ctf_event.pk, count=1, email="generated@test.com", actor=organizer_credential)
    create_participant_accounts(ctf_event.pk, count=2, actor=organizer_credential)

    intents = _registration_intents(ctf_event)
    assert intents.count() == 3
    assert {intent.status for intent in intents} == {"scheduled"}
    audiences = {pid for intent in intents for pid in intent.campaign.audience_spec["participant_ids"]}
    expected = set(
        CTFParticipant.objects.filter(
            event=ctf_event, email__in=["single@test.com", "ada@test.com", "generated@test.com"]
        ).values_list("pk", flat=True)
    )
    assert audiences == {str(pk) for pk in expected}
    for intent in intents:
        assert CTFScheduledTask.objects.filter(
            task_type=ScheduledTaskType.RELEASE_COMMUNICATION.value, metadata__intent_id=str(intent.pk)
        ).exists()


def test_rolled_back_seat_declares_nothing(ctf_event, organizer_credential):
    with pytest.raises(RuntimeError), transaction.atomic():
        add_participant(ctf_event.pk, "gone@test.com", "Gone", actor=organizer_credential)
        raise RuntimeError("caller failed after the seat")

    assert not CTFParticipant.objects.filter(email="gone@test.com").exists()
    assert not CommunicationCampaign.objects.filter(notice_kind="registration").exists()


def test_approved_request_gets_one_confirmation_email_with_event_details(
    authenticated_organizer_client, ctf_event, mailoutbox
):
    from ctf.models import CTFPublicRegistrationRequest

    request_row = CTFPublicRegistrationRequest.objects.create(
        event=ctf_event, name="ada-lovelace", email="ada@test.com"
    )
    response = authenticated_organizer_client.post(
        f"/api/v1/ctf/registration-requests/{request_row.pk}/disposition/", {"action": "approve"}
    )
    assert response.status_code == 200
    assert mailoutbox == []  # the request path never sends mail

    (intent,) = _registration_intents(ctf_event)
    assert _release(intent.pk)["outcome"] == "released"
    delivery_svc.run_once()

    (message,) = mailoutbox
    assert message.to == ["ada@test.com"]
    for rendered in (message.body, message.alternatives[0][0]):
        assert "registered for" in rendered
        assert ctf_event.name in rendered
        assert ctf_event.event_timezone in rendered
    assert "password" not in message.body.lower()


def test_registration_retry_returns_the_first_declaration(ctf_event, organizer_credential):
    participant = add_participant(ctf_event.pk, "ada@test.com", "Ada", actor=organizer_credential)
    (intent,) = _registration_intents(ctf_event)

    with transaction.atomic():
        replay = notification.send_registration_confirmation(participant.pk)

    assert replay["intent_id"] == str(intent.pk)
    assert _registration_intents(ctf_event).count() == 1


def test_unavailable_email_keeps_the_declaration_without_runnable_work(ctf_event, organizer_credential, settings):
    settings.EMAIL_DELIVERY_AVAILABLE = False
    participant = add_participant(ctf_event.pk, "ada@test.com", "Ada", actor=organizer_credential)

    campaign = CommunicationCampaign.objects.get(notice_kind="registration")
    assert campaign.audience_spec["participant_ids"] == [str(participant.pk)]
    assert not CommunicationIntent.objects.filter(campaign=campaign).exists()
    assert notification.send_registration_confirmation(participant.pk)["outcome"] == "channel_unavailable"


def test_a_legacy_inline_campaign_is_not_scheduled_again(ctf_event, organizer_credential, settings):
    from ctf.services.notification.ledger import stage_notice

    participant = _ready_participant(ctf_event)
    settings.EMAIL_DELIVERY_AVAILABLE = False
    legacy = stage_notice(
        ctf_event.pk,
        kind="range_ready",
        subject="Your range is ready",
        body="Open your event range page to access your range.",
        occurrence="42",
        participant_id=participant.pk,
    )
    settings.EMAIL_DELIVERY_AVAILABLE = True

    result = notification.send_range_ready(participant.pk)

    assert result == {"campaign_id": legacy["campaign_id"], "outcome": "existing"}
    assert not CommunicationIntent.objects.filter(campaign_id=legacy["campaign_id"]).exists()


def test_participant_notices_do_not_consume_the_authoring_budget(ctf_event, organizer_credential, settings):
    settings.CTF_COMMUNICATION_RATE_PER_ACTOR = 1
    settings.CTF_COMMUNICATION_RATE_PER_WORKSPACE = 1
    rows = "".join(f"P{index},p{index}@test.com\n" for index in range(3))

    created = bulk_import_participants(ctf_event.pk, f"name,email\n{rows}", actor=organizer_credential)["created"]

    assert len(created) == 3
    assert _registration_intents(ctf_event).count() == 3


def test_communication_refusal_never_fails_the_seat(ctf_event, organizer_credential, monkeypatch, caplog):
    from ctf.exceptions import CTFCommunicationError

    def refuse(*args, **kwargs):
        raise CTFCommunicationError("refused", code="CTF_COMMUNICATION_TARGET_DENIED")

    monkeypatch.setattr("ctf.services.communication.scheduling.schedule_declaration", refuse)
    with caplog.at_level(logging.WARNING, logger="ctf.services.notification.ledger"):
        participant = add_participant(ctf_event.pk, "ada@test.com", "Ada", actor=organizer_credential)

    assert CTFParticipant.objects.filter(pk=participant.pk).exists()
    assert not CommunicationCampaign.objects.filter(notice_kind="registration").exists()
    assert "CTF_COMMUNICATION_TARGET_DENIED" in caplog.text
    assert "ada@test.com" not in caplog.text


def test_delivered_ready_status_declares_range_ready_once(ctf_event_active):
    from cms.services import range_status_changed

    participant = _ready_participant(ctf_event_active)
    for _ in range(2):
        with transaction.atomic():
            range_status_changed.send(
                sender=None, range_instance_id=42, new_status="ready", previous_status="provisioning"
            )

    participant.refresh_from_db()
    assert participant.range_status == "ready"
    intents = CommunicationIntent.objects.filter(campaign__notice_kind="range_ready")
    assert intents.count() == 1
    assert intents.get().campaign.audience_spec["participant_ids"] == [str(participant.pk)]


def test_status_read_after_the_signal_declares_nothing_new(ctf_event_active, monkeypatch):
    from cms.services import range_status_changed
    from ctf.services.range.status import get_range_status

    participant = _ready_participant(ctf_event_active)
    with transaction.atomic():
        range_status_changed.send(sender=None, range_instance_id=42, new_status="ready", previous_status="provisioning")
    monkeypatch.setattr("ctf.bridges.cms_get_range_status", lambda _rid: "ready")
    get_range_status(participant.pk)

    assert CommunicationIntent.objects.filter(campaign__notice_kind="range_ready").count() == 1
