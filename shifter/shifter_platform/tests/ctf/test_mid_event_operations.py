"""Tests for mid-event CTF operations (issue #945)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from cms.models import RangeInstance
from ctf.enums import ChallengeCategory, ChallengeDifficulty, EventStatus, ScheduledTaskStatus, ScheduledTaskType
from ctf.models import CTFChallenge, CTFEvent, CTFFlag, CTFParticipant, CTFScheduledTask
from ctf.services import update_event
from ctf.services.challenge import add_flag, remove_flag, update_challenge, update_flag, verify_flag
from risk_register.models import AuditLog
from shared.audit import AuditAction
from shared.enums import RangeSource


@pytest.mark.django_db
class TestLiveEventEndReschedule:
    """Extending event_end during ACTIVE reschedules EVENT_END tasks."""

    def test_update_event_reschedules_event_end_during_active(self, ctf_event_active):
        old_end = ctf_event_active.event_end
        old_task = CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            scheduled_for=old_end,
            status=ScheduledTaskStatus.PENDING.value,
        )
        new_end = old_end + timedelta(hours=2)

        update_event(ctf_event_active.pk, {"event_end": new_end})

        old_task.refresh_from_db()
        assert old_task.status == ScheduledTaskStatus.CANCELLED.value

        new_task = CTFScheduledTask.objects.get(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            status=ScheduledTaskStatus.PENDING.value,
        )
        assert new_task.scheduled_for == new_end
        assert new_task.pk != old_task.pk

    def test_update_event_does_not_recreate_spinup_on_active(self, ctf_event_active):
        """Live reschedule must not add SPIN_UP / EVENT_START tasks mid-event."""
        CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            scheduled_for=ctf_event_active.event_end,
            status=ScheduledTaskStatus.PENDING.value,
        )
        new_end = ctf_event_active.event_end + timedelta(hours=1)
        update_event(ctf_event_active.pk, {"event_end": new_end})

        assert not CTFScheduledTask.objects.filter(
            event=ctf_event_active,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
            status=ScheduledTaskStatus.PENDING.value,
        ).exists()
        assert not CTFScheduledTask.objects.filter(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_START.value,
            status=ScheduledTaskStatus.PENDING.value,
        ).exists()

    def test_update_event_cancels_stale_cleanup_when_auto_cleanup_disabled(self, ctf_event_active):
        ctf_event_active.auto_cleanup = True
        ctf_event_active.save(update_fields=["auto_cleanup", "updated_at"])
        stale_cleanup = CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.CLEANUP_RANGES.value,
            scheduled_for=ctf_event_active.get_cleanup_time(),
            status=ScheduledTaskStatus.PENDING.value,
        )
        new_end = ctf_event_active.event_end + timedelta(hours=1)

        update_event(
            ctf_event_active.pk,
            {"event_end": new_end, "auto_cleanup": False},
        )

        stale_cleanup.refresh_from_db()
        assert stale_cleanup.status == ScheduledTaskStatus.CANCELLED.value
        assert not CTFScheduledTask.objects.filter(
            event=ctf_event_active,
            task_type=ScheduledTaskType.CLEANUP_RANGES.value,
            status=ScheduledTaskStatus.PENDING.value,
        ).exists()


@pytest.mark.django_db
class TestStaleEventEndHandler:
    """Stale EVENT_END tasks must not end an extended event."""

    def test_handle_event_end_skips_when_event_end_extended(self, ctf_event_active):
        from ctf.enums import EventStatus
        from ctf.management.commands.run_ctf_scheduler import _handle_event_end

        task = CTFScheduledTask.objects.create(
            event=ctf_event_active,
            task_type=ScheduledTaskType.EVENT_END.value,
            scheduled_for=timezone.now() - timedelta(minutes=5),
            status=ScheduledTaskStatus.PENDING.value,
        )
        ctf_event_active.event_end = timezone.now() + timedelta(hours=2)
        ctf_event_active.save(update_fields=["event_end", "updated_at"])

        _handle_event_end(task)

        ctf_event_active.refresh_from_db()
        task.refresh_from_db()
        assert ctf_event_active.status == EventStatus.ACTIVE.value
        assert task.status == ScheduledTaskStatus.CANCELLED.value


@pytest.mark.django_db
class TestLiveFlagRepair:
    """Organizers can repair flags on ACTIVE events with audit trail."""

    def test_add_flag_allowed_on_active_event(self, ctf_event_active):
        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Live Flag Challenge",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        flag_obj = add_flag(
            challenge.pk,
            {"flag": "FLAG{live_correct}"},
            actor_id=ctf_event_active.created_by_id,
        )
        assert flag_obj.pk is not None
        assert verify_flag(challenge, "FLAG{live_correct}")

    def test_update_flag_on_active_event(self, ctf_event_active):
        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Broken Flag Challenge",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        flag_obj = add_flag(
            challenge.pk,
            {"flag": "FLAG{broken}"},
            actor_id=ctf_event_active.created_by_id,
        )
        update_flag(
            flag_obj.pk,
            {"flag": "FLAG{fixed}"},
            actor_id=ctf_event_active.created_by_id,
        )
        challenge.refresh_from_db()
        assert verify_flag(challenge, "FLAG{fixed}")
        assert not verify_flag(challenge, "FLAG{broken}")

    def test_update_flag_writes_audit_row(self, ctf_event_active):
        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Audit Flag Challenge",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        flag_obj = add_flag(
            challenge.pk,
            {"flag": "FLAG{before}"},
            actor_id=ctf_event_active.created_by_id,
        )
        before_count = AuditLog.objects.count()
        update_flag(
            flag_obj.pk,
            {"flag": "FLAG{after}"},
            actor_id=ctf_event_active.created_by_id,
        )
        assert AuditLog.objects.count() == before_count + 1
        entry = AuditLog.objects.latest("timestamp")
        assert entry.action == AuditAction.UPDATE
        assert entry.new_state["challenge_id"] == str(challenge.pk)
        assert entry.new_state["flag_id"] == str(flag_obj.pk)
        assert set(entry.new_state.keys()) == {
            "ctf_live_flag_repair",
            "challenge_id",
            "flag_id",
            "event_id",
        }

    def test_update_challenge_rejects_non_flag_edits_on_active(self, ctf_event_active):
        from ctf.exceptions import CTFStateError

        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Original Name",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        with pytest.raises(CTFStateError):
            update_challenge(
                challenge.pk,
                {"name": "Renamed Mid Event"},
                actor_id=ctf_event_active.created_by_id,
            )

    def test_update_challenge_allows_flag_only_on_active(self, ctf_event_active):
        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Legacy Flag Challenge",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        update_challenge(
            challenge.pk,
            {"flag": "FLAG{legacy_fixed}"},
            actor_id=ctf_event_active.created_by_id,
        )
        challenge.refresh_from_db()
        assert verify_flag(challenge, "FLAG{legacy_fixed}")

    def test_remove_flag_allowed_on_active_event(self, ctf_event_active):
        challenge = CTFChallenge.objects.create(
            event=ctf_event_active,
            name="Remove Live Flag",
            description="Desc",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="placeholder",
        )
        flag_obj = add_flag(
            challenge.pk,
            {"flag": "FLAG{remove_me}"},
            actor_id=ctf_event_active.created_by_id,
        )
        remove_flag(flag_obj.pk, actor_id=ctf_event_active.created_by_id)
        assert not CTFFlag.objects.filter(pk=flag_obj.pk).exists()


@pytest.mark.django_db
class TestParticipantAccountEventIsolation:
    """One temporary account cannot be shared between active events."""

    def test_account_cannot_be_reused_across_active_events(
        self,
        participant_user,
        organizer_user,
    ):
        older_event = CTFEvent.objects.create(
            name="Older Event",
            description="Earlier event",
            created_by=organizer_user,
            status=EventStatus.ACTIVE.value,
            event_start=timezone.now() - timedelta(days=2),
            event_end=timezone.now() + timedelta(days=1),
            scenario_id="basic",
        )
        newer_event = CTFEvent.objects.create(
            name="Newer Event",
            description="Later event",
            created_by=organizer_user,
            status=EventStatus.ACTIVE.value,
            event_start=timezone.now() - timedelta(hours=1),
            event_end=timezone.now() + timedelta(hours=7),
            scenario_id="basic",
        )
        # Distinct range_source per row: the one-active-range-per-source
        # constraint (#307) forbids two active same-source ranges for one user.
        # The sources are incidental here -- this test asserts the CTFParticipant
        # `unique_active_ctf_participant_user` guard, which is itself why a user
        # can never legitimately hold two active CTF ranges across events.
        older_range = RangeInstance.objects.create(
            user_id=participant_user.pk,
            scenario_id="basic",
            status="ready",
            range_source=RangeSource.CTF.value,
        )
        newer_range = RangeInstance.objects.create(
            user_id=participant_user.pk,
            scenario_id="basic",
            status="provisioning",
            range_source=RangeSource.MISSION_CONTROL.value,
        )
        older_participant = CTFParticipant.objects.create(
            event=older_event,
            user=participant_user,
            email=participant_user.email,
            name="Older Participant",
            status="active",
            registered_at=timezone.now(),
            range_instance_id=older_range.pk,
            range_status="ready",
        )
        with pytest.raises(ValidationError, match="unique_active_ctf_participant_user"):
            CTFParticipant.objects.create(
                event=newer_event,
                user=participant_user,
                email=participant_user.email,
                name="Newer Participant",
                status="active",
                registered_at=timezone.now(),
                range_instance_id=newer_range.pk,
                range_status="provisioning",
            )
        assert older_participant.range_instance_id == older_range.pk


@pytest.mark.django_db
class TestDisqualifyIsolatedAccount:
    """Disqualification retires the event-scoped temporary identity."""

    def _event(self, organizer_user, name):
        return CTFEvent.objects.create(
            name=name,
            description=name,
            created_by=organizer_user,
            status=EventStatus.ACTIVE.value,
            event_start=timezone.now() - timedelta(hours=2),
            event_end=timezone.now() + timedelta(hours=6),
            scenario_id="basic",
        )

    def test_same_account_cannot_join_two_active_events(self, participant_user, organizer_user):
        event_a = self._event(organizer_user, "Event A")
        event_b = self._event(organizer_user, "Event B")
        CTFParticipant.objects.create(
            event=event_a,
            user=participant_user,
            email=participant_user.email,
            name="P",
            status="active",
            registered_at=timezone.now(),
        )
        with pytest.raises(ValidationError, match="unique_active_ctf_participant_user"):
            CTFParticipant.objects.create(
                event=event_b,
                user=participant_user,
                email=participant_user.email,
                name="P",
                status="active",
                registered_at=timezone.now(),
            )

    def test_disqualify_clears_group_when_no_other_participation(self, participant_user, organizer_user):
        from ctf.services.participant import disqualify_participant
        from management.services import configure_temporary_ctf_account, get_user_profile
        from shared.auth import is_ctf_participant

        event_a = self._event(organizer_user, "Solo Event")
        part_a = CTFParticipant.objects.create(
            event=event_a,
            user=participant_user,
            email=participant_user.email,
            name="P",
            status="active",
            registered_at=timezone.now(),
        )
        configure_temporary_ctf_account(participant_user, event_a.pk)

        disqualify_participant(part_a.id)

        participant_user.refresh_from_db()
        assert is_ctf_participant(participant_user) is False
        assert participant_user.is_active is False
        assert participant_user.username.startswith("ctf-tombstone-")
        assert get_user_profile(participant_user).active_ctf_event_id is None
