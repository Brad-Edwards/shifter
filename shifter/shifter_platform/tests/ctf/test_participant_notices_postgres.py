"""PostgreSQL proof of range-ready lock order (ADR-065-R5, #664).

SQLite cannot prove ``select_for_update`` ordering. The CMS status delivery
declares range-ready, which locks the event. Provisioning and recovery lock the
event, then the participant. The delivery must use the same order, or the two
transactions deadlock.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction

from cms.handlers.ctf_bridge import notify_ctf_range_status
from ctf.models import CommunicationIntent, CTFEvent, CTFParticipant

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def test_ready_delivery_and_event_first_claim_do_not_deadlock(ctf_event_active, settings, monkeypatch):
    settings.EMAIL_DELIVERY_AVAILABLE = True
    monkeypatch.setattr("ctf.services.communication.metrics._resolve_client", lambda: None)
    participant = CTFParticipant.objects.create(
        event=ctf_event_active,
        email="racer@test.com",
        name="racer",
        status="active",
        range_instance_id=42,
        range_status="provisioning",
    )
    event_locked = threading.Event()

    def event_first_claim():
        # The provisioning and recovery claim order: event, then participant.
        try:
            with transaction.atomic():
                CTFEvent.objects.select_for_update().get(pk=ctf_event_active.pk)
                event_locked.set()
                time.sleep(0.5)  # let the delivery reach its first lock wait
                CTFParticipant.objects.select_for_update().get(pk=participant.pk)
        finally:
            connection.close()

    def ready_delivery():
        try:
            event_locked.wait(timeout=5)
            with transaction.atomic():
                notify_ctf_range_status(42, "ready", "provisioning")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(event_first_claim)
        delivery = pool.submit(ready_delivery)
        claim.result(timeout=30)
        delivery.result(timeout=30)

    participant.refresh_from_db()
    assert participant.range_status == "ready"
    assert CommunicationIntent.objects.filter(campaign__notice_kind="range_ready").count() == 1
