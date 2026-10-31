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

    @pytest.mark.parametrize(
        "stored,expected",
        [([], []), ([48, 48, 30], [30, 48]), ([True, 24], [24])],
    )
    def test_open_registration_schedules_the_stored_reminder_hours(
        self, ctf_event_draft, authenticated_organizer_client, stored, expected
    ):
        """An empty list disables reminders; duplicates and booleans add no task (#582)."""
        ctf_event_draft.reminder_hours = stored
        ctf_event_draft.save(update_fields=["reminder_hours", "updated_at"])

        assert _lifecycle(authenticated_organizer_client, ctf_event_draft, "open_registration").status_code == 200

        reminders = CTFScheduledTask.objects.filter(
            event=ctf_event_draft, task_type=ScheduledTaskType.SEND_REMINDER.value
        )
        assert sorted(task.metadata["hours_before"] for task in reminders) == expected

    def test_changing_reminder_hours_after_registration_opens_replaces_pending_reminders(
        self, ctf_event_draft, authenticated_organizer_client
    ):
        """Clearing the intervals cancels reminders already queued (#582)."""
        assert _lifecycle(authenticated_organizer_client, ctf_event_draft, "open_registration").status_code == 200
        url = f"/api/v1/ctf/events/{ctf_event_draft.pk}/"
        pending = CTFScheduledTask.objects.filter(event=ctf_event_draft, status=ScheduledTaskStatus.PENDING.value)

        def pending_reminder_hours():
            reminders = pending.filter(task_type=ScheduledTaskType.SEND_REMINDER.value)
            return sorted(task.metadata["hours_before"] for task in reminders)

        def other_pending_ids():
            return set(pending.exclude(task_type=ScheduledTaskType.SEND_REMINDER.value).values_list("pk", flat=True))

        others = other_pending_ids()
        assert pending_reminder_hours() == [1, 24]

        changed = authenticated_organizer_client.put(url, {"reminder_hours": [6]}, content_type="application/json")
        assert changed.status_code == 200
        assert pending_reminder_hours() == [6]

        cleared = authenticated_organizer_client.put(url, {"reminder_hours": []}, content_type="application/json")
        assert cleared.status_code == 200
        assert pending_reminder_hours() == []
        assert other_pending_ids() == others

    def test_new_events_remind_24_hours_and_1_hour_before_start(self, organizer_user):
        from ctf.models import CTFEvent
        from tests.ctf.conftest import individual_event_scope

        event = CTFEvent.objects.create(
            name="Defaults",
            created_by=organizer_user,
            **individual_event_scope(organizer_user),
            event_start=timezone.now() + timedelta(days=2),
            event_end=timezone.now() + timedelta(days=2, hours=8),
        )
        assert event.reminder_hours == [24, 1]

    def test_migration_keeps_reminders_for_upcoming_events_that_stored_an_empty_list(
        self, ctf_event_draft, ctf_event_active
    ):
        import importlib

        from django.apps import apps

        from ctf.models import CTFEvent

        CTFEvent.objects.filter(pk__in=[ctf_event_draft.pk, ctf_event_active.pk]).update(reminder_hours=[])
        migration = importlib.import_module("ctf.migrations.0084_recipient_time_zone_reminder_default")

        migration.keep_default_reminders(apps, None)

        ctf_event_draft.refresh_from_db()
        ctf_event_active.refresh_from_db()
        assert ctf_event_draft.reminder_hours == [24, 1]
        assert ctf_event_active.reminder_hours == []

    def test_event_writes_refuse_an_unknown_time_zone(self, ctf_event_draft, authenticated_organizer_client):
        url = f"/api/v1/ctf/events/{ctf_event_draft.pk}/"
        put = authenticated_organizer_client.put

        refused = put(url, {"event_timezone": "Mars/Olympus"}, content_type="application/json")
        accepted = put(url, {"event_timezone": "Europe/Paris"}, content_type="application/json")

        assert refused.status_code == 400
        assert accepted.status_code == 200
        ctf_event_draft.refresh_from_db()
        assert ctf_event_draft.event_timezone == "Europe/Paris"

    def test_open_registration_schedules_automation(self, ctf_event_draft, authenticated_organizer_client):
        resp = _lifecycle(authenticated_organizer_client, ctf_event_draft, "open_registration")
        assert resp.status_code == 200
        types = set(CTFScheduledTask.objects.filter(event=ctf_event_draft).values_list("task_type", flat=True))
        assert ScheduledTaskType.EVENT_START.value in types
        assert ScheduledTaskType.EVENT_END.value in types
        assert ScheduledTaskType.CLEANUP_RANGES.value in types
        assert ScheduledTaskType.CLEANUP_WARNING.value in types

    def test_cancel_notifies_participants(self, ctf_event_active, authenticated_organizer_client, monkeypatch):
        sent = {}

        def fake_announce(event_id, subject, body, created_by):
            sent.update({"event_id": event_id, "subject": subject})

        monkeypatch.setattr("ctf.services.notification.send_announcement", fake_announce)
        CTFParticipant.objects.create(
            event=ctf_event_active,
            email="p@test.com",
            name="P",
            status="active",
            registered_at=timezone.now(),
        )
        resp = _lifecycle(authenticated_organizer_client, ctf_event_active, "cancel")
        assert resp.json()["status"] == "cancelled"
        assert sent["event_id"] == ctf_event_active.pk
        assert "cancelled" in sent["subject"]


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
