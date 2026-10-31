"""Event lifecycle, scheduler-control, and cleanup API flows (CTF-007, #526, CTF-1003)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from ctf.enums import ScheduledTaskStatus, ScheduledTaskType
from ctf.models import CTFParticipant, CTFScheduledTask
from tests.ctf._api_flow_helpers import call_json

pytestmark = pytest.mark.django_db


def _lifecycle(client, event, action):
    return call_json(client, "post", "api_event_lifecycle", kwargs={"event_id": event.id}, body={"action": action})


class TestLifecycleActions:
    def test_full_transition_chain(self, ctf_event_draft, authenticated_organizer_client):
        ctf_event = ctf_event_draft
        assert _lifecycle(authenticated_organizer_client, ctf_event, "open_registration").json()["status"] == (
            "registration"
        )
        assert _lifecycle(authenticated_organizer_client, ctf_event, "activate").json()["status"] == "active"
        assert _lifecycle(authenticated_organizer_client, ctf_event, "pause").json()["status"] == "paused"
        assert _lifecycle(authenticated_organizer_client, ctf_event, "resume").json()["status"] == "active"
        assert _lifecycle(authenticated_organizer_client, ctf_event, "end").json()["status"] == "ended"

    def test_invalid_transition_conflicts(self, ctf_event_draft, authenticated_organizer_client):
        resp = _lifecycle(authenticated_organizer_client, ctf_event_draft, "activate")
        assert resp.status_code == 409

    def test_open_registration_schedules_automation(self, ctf_event_draft, authenticated_organizer_client):
        resp = _lifecycle(authenticated_organizer_client, ctf_event_draft, "open_registration")
        assert resp.status_code == 200
        types = set(CTFScheduledTask.objects.filter(event=ctf_event_draft).values_list("task_type", flat=True))
        assert ScheduledTaskType.EVENT_START.value in types
        assert ScheduledTaskType.EVENT_END.value in types
        assert ScheduledTaskType.CLEANUP_RANGES.value in types
        assert ScheduledTaskType.CLEANUP_WARNING.value in types


class _NullPublisher:
    def put_metric_data(self, **kwargs: object) -> None:
        return None


class TestCancellationNotice:
    """CTF-706: the cancellation notice goes through the real ledger, release task and worker."""

    @pytest.fixture(autouse=True)
    def _delivery_environment(self, settings, monkeypatch):
        import boto3

        settings.SITE_URL = "https://ctf.example.test"
        settings.CLOUD_PROVIDER = "aws"
        # The worker's metrics go to the cloud SDK; fake it at that external boundary.
        monkeypatch.setattr(boto3, "client", lambda name: _NullPublisher())

    @staticmethod
    def _participant(event, name, **fields):
        values = {"status": "active", "registered_at": timezone.now(), **fields}
        return CTFParticipant.objects.create(event=event, email=f"{name}@test.com", name=name, **values)

    def test_cancel_emails_each_registered_participant(
        self, ctf_event_active, authenticated_organizer_client, mailoutbox
    ):
        from ctf.services.communication import delivery, run_release_communication_task

        self._participant(ctf_event_active, "ada")
        self._participant(ctf_event_active, "bob")
        self._participant(ctf_event_active, "invited", registered_at=None)

        resp = _lifecycle(authenticated_organizer_client, ctf_event_active, "cancel")
        assert resp.json()["status"] == "cancelled"

        task = CTFScheduledTask.objects.get(
            event=ctf_event_active,
            task_type=ScheduledTaskType.RELEASE_COMMUNICATION.value,
            status=ScheduledTaskStatus.PENDING.value,
        )
        assert run_release_communication_task(task)["outcome"] == "released"
        delivery.run_once()

        assert sorted(message.to[0] for message in mailoutbox) == ["ada@test.com", "bob@test.com"]
        message = mailoutbox[0]
        assert message.subject == "Event cancelled"
        assert f"The organizer cancelled {ctf_event_active.name}." in message.body
        assert f"The organizer cancelled {ctf_event_active.name}." in message.alternatives[0][0]

    def test_only_the_cancellation_notice_passes_the_fence(self, ctf_event_active, authenticated_organizer_client):
        from uuid import uuid4

        from ctf.exceptions import CTFCommunicationError
        from ctf.services.communication import AdmissionActor, CampaignDraft, release_campaign
        from ctf.services.communication.campaigns import create_system_event_campaign

        def draft(kind):
            return CampaignDraft(
                title="Notice",
                origin="system_milestone",
                target_event_ids=[ctf_event_active.pk],
                audience_spec={"kind": "event", "event_ids": [str(ctf_event_active.pk)]},
                trigger_spec={"kind": "manual"},
                channels=["email"],
                subject="Notice",
                body="Fixed text.",
                notice_kind=kind,
            )

        with pytest.raises(CTFCommunicationError) as live:
            create_system_event_campaign(ctf_event_active, draft("cancellation"), campaign_id=uuid4())
        assert live.value.code == "CTF_COMMUNICATION_EVENT_NOT_CANCELLED"

        self._participant(ctf_event_active, "ada")
        _lifecycle(authenticated_organizer_client, ctf_event_active, "cancel")
        ctf_event_active.refresh_from_db()
        announcement = create_system_event_campaign(ctf_event_active, draft("announcement"), campaign_id=uuid4())
        with pytest.raises(CTFCommunicationError) as refused:
            release_campaign(announcement, occurrence_key="notice", admission=AdmissionActor(system=True))
        assert refused.value.code == "CTF_COMMUNICATION_EVENT_CANCELLED"

    def test_worker_still_suppresses_ordinary_mail_for_a_cancelled_event(self, ctf_event_active, mailoutbox):
        from uuid import uuid4

        from ctf.enums_communication import DeliveryStatus
        from ctf.models import CTFEvent, DeliveryAttempt
        from ctf.services.communication import AdmissionActor, CampaignDraft, delivery, release_campaign
        from ctf.services.communication.campaigns import create_system_event_campaign

        self._participant(ctf_event_active, "ada")
        draft = CampaignDraft(
            title="Notice",
            origin="system_milestone",
            target_event_ids=[ctf_event_active.pk],
            audience_spec={"kind": "event", "event_ids": [str(ctf_event_active.pk)]},
            trigger_spec={"kind": "manual"},
            channels=["email"],
            subject="Notice",
            body="Fixed text.",
        )
        campaign = create_system_event_campaign(ctf_event_active, draft, campaign_id=uuid4())
        intent = release_campaign(campaign, occurrence_key="notice", admission=AdmissionActor(system=True))
        # The cancellation lands after release, as it can after a worker claim.
        CTFEvent.objects.filter(pk=ctf_event_active.pk).update(status="cancelled")

        delivery.run_once()

        assert mailoutbox == []
        attempt = DeliveryAttempt.objects.get(intent=intent)
        assert attempt.status == DeliveryStatus.SUPPRESSED.value


class TestTaskControls:
    @pytest.fixture
    def scheduled(self, ctf_event_draft, authenticated_organizer_client):
        _lifecycle(authenticated_organizer_client, ctf_event_draft, "open_registration")
        return ctf_event_draft, authenticated_organizer_client

    def test_task_listing(self, scheduled):
        event, client = scheduled
        resp = call_json(client, "get", "api_event_tasks", kwargs={"event_id": event.id})
        assert resp.status_code == 200
        types = [t["task_type"] for t in resp.json()["tasks"]]
        assert ScheduledTaskType.EVENT_START.value in types

    def test_run_now_makes_task_due(self, scheduled):
        event, client = scheduled
        task = CTFScheduledTask.objects.filter(event=event, task_type=ScheduledTaskType.EVENT_END.value).first()
        resp = call_json(client, "post", "api_event_task_run", kwargs={"event_id": event.id, "task_id": task.id})
        assert resp.status_code == 200
        task.refresh_from_db()
        assert task.scheduled_for <= timezone.now()
        assert task.is_due

    def test_run_now_rejects_completed_task(self, scheduled):
        event, client = scheduled
        task = CTFScheduledTask.objects.filter(event=event).first()
        task.mark_completed()
        resp = call_json(client, "post", "api_event_task_run", kwargs={"event_id": event.id, "task_id": task.id})
        assert resp.status_code == 409

    def test_defer_and_cancel_cleanup(self, scheduled):
        event, client = scheduled
        cleanup = CTFScheduledTask.objects.get(event=event, task_type=ScheduledTaskType.CLEANUP_RANGES.value)
        before = cleanup.scheduled_for

        resp = call_json(
            client, "post", "api_event_cleanup", kwargs={"event_id": event.id}, body={"action": "defer", "hours": 6}
        )
        assert resp.status_code == 200
        cleanup.refresh_from_db()
        assert cleanup.scheduled_for == before + timedelta(hours=6)

        resp = call_json(client, "post", "api_event_cleanup", kwargs={"event_id": event.id}, body={"action": "cancel"})
        assert resp.status_code == 200
        cleanup.refresh_from_db()
        assert cleanup.status == ScheduledTaskStatus.CANCELLED.value

        # Nothing pending anymore: further control attempts conflict.
        resp = call_json(client, "post", "api_event_cleanup", kwargs={"event_id": event.id}, body={"action": "cancel"})
        assert resp.status_code == 409

    def test_service_cleanup_admits_before_atomic_mutation_and_audits_principal(self, scheduled, settings, monkeypatch):
        """Service cleanup keeps provider calls outside its atomic mutation and audit."""
        from types import SimpleNamespace

        from django.db import connection
        from rest_framework.test import APIClient

        from shared.authorization import AuthorizationDecision, DecisionKind, port
        from shared.models import AuditLog
        from tests.cms._warm_pool_fixtures import service_credential

        event, _ = scheduled
        credential = service_credential(settings, scopes=["authorization:event.manage_lifecycle", "ctf:event:write"])
        # The rollback fixture owns an outer transaction; the request must not
        # add its mutation transaction until provider admission is complete.
        outer_transactions = tuple(connection.atomic_blocks)

        def check(request):
            assert tuple(connection.atomic_blocks) == outer_transactions
            assert request.principal == credential.principal
            return AuthorizationDecision(DecisionKind.ALLOWED, "policy_allowed")

        monkeypatch.setattr(port, "_provider_factory", lambda: SimpleNamespace(check=check))
        client = APIClient()
        client.force_authenticate(user=None, token=credential)
        response = call_json(
            client, "post", "api_event_cleanup", kwargs={"event_id": event.pk}, body={"action": "cancel"}
        )
        assert response.status_code == 200, response.content
        assert CTFScheduledTask.objects.get(event=event, task_type="cleanup_ranges").status == "cancelled"
        row = AuditLog.objects.get(context="ctf_event_action", new_state__operation="event.cleanup.cancel")
        assert row.actor_type == "principal" and row.actor_id is None
        assert row.actor_principal_uuid == credential.principal.uuid
        assert row.new_state["authority_source"] == "policy"


class TestSchedulerRetry:
    def test_retry_backs_off_then_fails(self, ctf_event):
        task = CTFScheduledTask.objects.create(
            event=ctf_event,
            task_type=ScheduledTaskType.SEND_REMINDER.value,
            scheduled_for=timezone.now(),
            max_retries=2,
        )
        assert task.retry_or_fail("boom-1") is True
        task.refresh_from_db()
        assert task.status == ScheduledTaskStatus.PENDING.value
        assert task.retry_count == 1
        assert task.scheduled_for > timezone.now()

        assert task.retry_or_fail("boom-2") is True
        task.refresh_from_db()
        assert task.retry_count == 2

        assert task.retry_or_fail("boom-3") is False
        task.refresh_from_db()
        assert task.status == ScheduledTaskStatus.FAILED.value
        assert task.error_message == "boom-3"


class TestEventEndCleanupWindow:
    def test_event_end_defers_to_pending_cleanup_task(self, ctf_event_active, monkeypatch):
        """The delayed-cleanup review window (CTF-703) is honored at event end."""
        from ctf.management.commands.run_ctf_scheduler import _handle_event_end

        ctf_event_active.event_end = timezone.now() - timedelta(minutes=1)
        ctf_event_active.save(update_fields=["event_end", "updated_at"])
        CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.CLEANUP_RANGES.value,
            scheduled_for=timezone.now() + timedelta(hours=2),
        )
        end_task = CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            scheduled_for=timezone.now(),
        )
        called = []
        monkeypatch.setattr("ctf.services.range.cleanup_event_ranges", lambda event_id: called.append(event_id))

        _handle_event_end(end_task)

        ctf_event_active.refresh_from_db()
        assert ctf_event_active.status == "ended"
        assert called == []

    def test_event_end_cleans_up_without_pending_task(self, ctf_event_active, monkeypatch):
        from ctf.management.commands.run_ctf_scheduler import _handle_event_end

        ctf_event_active.event_end = timezone.now() - timedelta(minutes=1)
        ctf_event_active.save(update_fields=["event_end", "updated_at"])
        end_task = CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            scheduled_for=timezone.now(),
        )
        called = []
        monkeypatch.setattr("cms.services.expire_due_ranges", lambda: called.append("due") or {"expired": 0})

        _handle_event_end(end_task)

        assert called == ["due"]


class TestCleanupWarning:
    def test_warning_email_counts(self, ctf_event_active, monkeypatch, settings):
        settings.EMAIL_DELIVERY_AVAILABLE = False
        from ctf.services.notification import send_cleanup_warning

        CTFParticipant.objects.create(
            event=ctf_event_active,
            email="warn@test.com",
            name="W",
            status="active",
            registered_at=timezone.now(),
        )
        sent = []
        monkeypatch.setattr(
            "ctf.services.notification._send_email",
            lambda **kwargs: sent.append(kwargs["recipient"]),
        )
        result = send_cleanup_warning(ctf_event_active.pk)
        assert result["outcome"] == "channel_unavailable"
        assert sent == []
