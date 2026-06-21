"""Tests for CTF Range service.

Unit tests — mock all ORM access. We test our service logic
(branching, error wrapping, return values), not SQLite.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from ctf.bridges import RangeProvisionResult
from ctf.enums import ParticipantStatus, ScheduledTaskStatus, ScheduledTaskType
from ctf.exceptions import CTFNotFoundError, CTFRangeError
from ctf.models import CTFEvent, CTFParticipant, CTFScheduledTask
from ctf.services import range as range_service


def _make_unregistered_participant(event, idx):
    """Create an unassigned, unregistered participant on ``event``.

    With no linked user, provisioning fails fast ("must be registered") before
    any CMS/network call, so the throttled loop runs without external boundaries.
    """
    return CTFParticipant.objects.create(
        event=event,
        user=None,
        email=f"throttle-{idx}@test.com",
        name=f"Throttle Participant {idx}",
        status=ParticipantStatus.INVITED.value,
        invite_token=f"throttle-{event.pk}-{idx}",
        invite_token_expires=timezone.now() + timedelta(days=7),
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_participant():
    """Mock CTFParticipant with sensible defaults."""
    p = Mock(spec=CTFParticipant)
    p.pk = uuid4()
    p.range_instance_id = None
    p.range_status = ""
    p.user = Mock(email="participant@test.com")
    p.event = Mock(scenario_id="basic", range_config=None)
    return p


@pytest.fixture
def mock_participant_with_range(mock_participant):
    """Mock participant that already has a range assigned."""
    mock_participant.range_instance_id = 42
    mock_participant.range_status = "ready"
    return mock_participant


@pytest.fixture
def _patch_participant_get(mock_participant):
    """Patch CTFParticipant.objects so .get() and .select_related().get() return mock_participant."""
    with patch.object(CTFParticipant, "objects") as mock_objects:
        mock_objects.get.return_value = mock_participant
        mock_objects.select_related.return_value.get.return_value = mock_participant
        mock_objects.DoesNotExist = CTFParticipant.DoesNotExist
        yield mock_objects


@pytest.fixture
def _patch_participant_not_found():
    """Patch CTFParticipant.objects so .get() raises DoesNotExist."""
    with patch.object(CTFParticipant, "objects") as mock_objects:
        mock_objects.get.side_effect = CTFParticipant.DoesNotExist
        mock_objects.select_related.return_value.get.side_effect = CTFParticipant.DoesNotExist
        mock_objects.DoesNotExist = CTFParticipant.DoesNotExist
        yield mock_objects


class TestProvisionParticipantRange:
    """Tests for provision_participant_range.

    DB-backed (#942): assignment now runs under ``transaction.atomic()`` +
    ``select_for_update()`` to close the manual/scheduled double-assign race,
    so these exercise real rows rather than a mocked ``objects`` manager.
    """

    @pytest.mark.django_db
    def test_not_found(self):
        """Raises CTFNotFoundError for nonexistent participant."""
        with pytest.raises(CTFNotFoundError):
            range_service.provision_participant_range(uuid4())

    @pytest.mark.django_db
    def test_already_assigned_raises_and_keeps_assignment(self, ctf_participant):
        """An already-assigned participant raises without re-provisioning (CTF-7).

        No CMS mock: if the guard regressed and the code reached provisioning,
        the real bridge would raise "Range provisioning failed" and the
        already-has-a-range match would not be met. The original assignment must
        survive unchanged.
        """
        ctf_participant.range_instance_id = 42
        ctf_participant.save(update_fields=["range_instance_id", "updated_at"])

        with pytest.raises(CTFRangeError, match="already has a range"):
            range_service.provision_participant_range(ctf_participant.pk)

        ctf_participant.refresh_from_db()
        assert ctf_participant.range_instance_id == 42

    @pytest.mark.django_db
    def test_provision_success_persists_assignment(self, ctf_participant):
        """Successful provisioning persists range_instance_id and status under the lock."""
        mock_result = RangeProvisionResult(request_id=uuid4())

        with (
            patch("ctf.bridges.cms_create_range", return_value=mock_result) as mock_create,
            patch("ctf.bridges.cms_find_range_instance_id", return_value=99),
        ):
            result = range_service.provision_participant_range(ctf_participant.pk)

        assert result["status"] == "provisioning"
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["user"] == ctf_participant.user

        ctf_participant.refresh_from_db()
        assert ctf_participant.range_instance_id == 99
        assert ctf_participant.range_status == "provisioning"

    @pytest.mark.django_db
    def test_in_flight_provisioning_blocks_reprovision(self, ctf_participant):
        """A participant mid-provision (status set, no instance id) is treated as claimed (#942).

        cms_create_range can succeed while cms_find_range_instance_id returns
        None, leaving range_status="provisioning" with a null range_instance_id.
        Keying the guard only on range_instance_id would let the next caller
        create a second CMS range. No CMS mock here: if the guard regressed the
        real bridge would raise "Range provisioning failed", not the benign
        "already has a range".
        """
        ctf_participant.range_status = "provisioning"
        ctf_participant.save(update_fields=["range_status", "updated_at"])

        with pytest.raises(CTFRangeError, match="already has a range"):
            range_service.provision_participant_range(ctf_participant.pk)

        ctf_participant.refresh_from_db()
        assert ctf_participant.range_instance_id is None
        assert ctf_participant.range_status == "provisioning"

    @pytest.mark.django_db
    def test_provision_requires_registered_user(self, ctf_participant_invited):
        """Raises CTFRangeError if participant has no linked user."""
        with pytest.raises(CTFRangeError, match="must be registered"):
            range_service.provision_participant_range(ctf_participant_invited.pk)

    @pytest.mark.django_db
    def test_provision_cms_failure(self, ctf_participant):
        """CMS errors are wrapped in CTFRangeError."""
        with (
            patch("ctf.bridges.cms_create_range", side_effect=RuntimeError("CMS down")),
            pytest.raises(CTFRangeError, match="Range provisioning failed"),
        ):
            range_service.provision_participant_range(ctf_participant.pk)


class TestGetRangeStatus:
    """Tests for get_range_status."""

    def test_not_found(self, _patch_participant_not_found):
        """Raises CTFNotFoundError for nonexistent participant."""
        with pytest.raises(CTFNotFoundError):
            range_service.get_range_status(uuid4())

    @pytest.mark.usefixtures("_patch_participant_get")
    def test_not_assigned(self, mock_participant):
        """Returns not_assigned when no range."""
        result = range_service.get_range_status(mock_participant.pk)
        assert result["status"] == "not_assigned"

    @pytest.mark.usefixtures("_patch_participant_get")
    def test_polls_cms(self, mock_participant):
        """Queries CMS for fresh status and updates cache."""
        mock_participant.range_instance_id = 42
        mock_participant.range_status = "provisioning"

        with patch("ctf.bridges.cms_get_range_status", return_value="ready"):
            result = range_service.get_range_status(mock_participant.pk)

        assert result["status"] == "ready"
        assert mock_participant.range_status == "ready"
        mock_participant.save.assert_called_once()


class TestCleanupEventRanges:
    """Tests for cleanup_event_ranges."""

    def test_not_found(self):
        """Raises CTFNotFoundError for nonexistent event."""
        from ctf.models import CTFEvent

        with patch.object(CTFEvent, "objects") as mock_objects:
            mock_objects.get.side_effect = CTFEvent.DoesNotExist
            mock_objects.DoesNotExist = CTFEvent.DoesNotExist
            with pytest.raises(CTFNotFoundError):
                range_service.cleanup_event_ranges(uuid4())

    def test_destroys_ranges(self):
        """Destroys all assigned ranges using participant.user."""
        from ctf.models import CTFEvent

        event_id = uuid4()
        mock_user = Mock()
        mock_participant = Mock(
            pk=uuid4(),
            range_instance_id=42,
            range_status="ready",
            user=mock_user,
        )

        with (
            patch.object(CTFEvent, "objects") as mock_event_objects,
            patch.object(CTFParticipant, "objects") as mock_part_objects,
            patch("ctf.bridges.cms_destroy_range") as mock_destroy,
        ):
            mock_event_objects.get.return_value = Mock()
            mock_part_objects.filter.return_value.select_related.return_value = [mock_participant]

            result = range_service.cleanup_event_ranges(event_id)

        assert result["destroyed"] == 1
        mock_destroy.assert_called_once_with(mock_user, 42)
        mock_participant.save.assert_called_once()
        assert mock_participant.range_instance_id is None
        assert mock_participant.range_status == ""


class TestDestroyParticipantRange:
    """Tests for destroy_participant_range."""

    def test_not_found(self, _patch_participant_not_found):
        """Raises CTFNotFoundError for nonexistent participant."""
        with pytest.raises(CTFNotFoundError):
            range_service.destroy_participant_range(uuid4())

    @pytest.mark.usefixtures("_patch_participant_get")
    def test_no_range(self, mock_participant):
        """Raises CTFRangeError when no range assigned."""
        with pytest.raises(CTFRangeError, match="No range assigned"):
            range_service.destroy_participant_range(mock_participant.pk)

    @pytest.mark.usefixtures("_patch_participant_get")
    def test_destroy_success(self, mock_participant):
        """Successfully destroys a participant's range."""
        mock_participant.range_instance_id = 42
        mock_participant.range_status = "ready"

        with patch("ctf.bridges.cms_destroy_range") as mock_destroy:
            result = range_service.destroy_participant_range(mock_participant.pk)

        assert result["status"] == "destroyed"
        mock_destroy.assert_called_once_with(mock_participant.user, 42)
        mock_participant.save.assert_called_once()
        assert mock_participant.range_instance_id is None


# ---------------------------------------------------------------------------
# Throttled provisioning fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_event_exists():
    """Patch CTFEvent.objects so .get() succeeds."""
    with patch.object(CTFEvent, "objects") as mock_objects:
        mock_objects.get.return_value = Mock()
        mock_objects.DoesNotExist = CTFEvent.DoesNotExist
        yield mock_objects


@pytest.fixture
def _patch_event_not_found():
    """Patch CTFEvent.objects so .get() raises DoesNotExist."""
    with patch.object(CTFEvent, "objects") as mock_objects:
        mock_objects.get.side_effect = CTFEvent.DoesNotExist
        mock_objects.DoesNotExist = CTFEvent.DoesNotExist
        yield mock_objects


@pytest.fixture
def _patch_sleep():
    """Patch time.sleep to avoid real delays."""
    with patch("ctf.services.range.time.sleep") as mock_sleep:
        yield mock_sleep


@pytest.fixture
def throttle_participants():
    """Create a list of mock participants for throttled provisioning tests."""
    return [Mock(pk=uuid4()) for _ in range(3)]


class TestProvisionEventRangesThrottled:
    """Tests for provision_event_ranges_throttled."""

    def test_not_found(self, _patch_event_not_found):
        """Raises CTFNotFoundError for nonexistent event."""
        with pytest.raises(CTFNotFoundError):
            range_service.provision_event_ranges_throttled(uuid4(), 300)

    @pytest.mark.usefixtures("_patch_event_exists")
    def test_empty_participants(self):
        """Returns zeros when no participants need provisioning."""
        with patch.object(CTFParticipant, "objects") as mock_part:
            mock_part.filter.return_value = []

            result = range_service.provision_event_ranges_throttled(uuid4(), 300)

        assert result["total"] == 0
        assert result["successful"] == 0
        assert result["failed"] == 0
        assert result["interrupted"] is False

    @pytest.mark.usefixtures("_patch_event_exists")
    def test_all_succeed_with_progress(self, throttle_participants, _patch_sleep):
        """Happy path: all provisions succeed, sleeps between them, logs progress."""
        event_id = uuid4()

        with (
            patch.object(CTFParticipant, "objects") as mock_part,
            patch.object(
                range_service,
                "provision_participant_range_with_retry",
                return_value={"status": "provisioning", "retries": 0},
            ) as mock_provision,
            patch("ctf.services.range.logger") as mock_logger,
        ):
            mock_part.filter.return_value = throttle_participants

            result = range_service.provision_event_ranges_throttled(event_id, 300)

        assert result["successful"] == 3
        assert result["failed"] == 0
        assert result["total"] == 3
        assert result["interrupted"] is False
        assert mock_provision.call_count == 3
        # Two inter-provision gaps (not after the last one). Each gap is slept
        # in <=15s heartbeat chunks, so total slept == 2 * clamped delay.
        # window=300 / 3 participants = 100s raw -> 100s clamped.
        assert sum(c.args[0] for c in _patch_sleep.call_args_list) == 200.0

        # Verify progress logging (3 progress calls among the info calls)
        progress_calls = [c for c in mock_logger.info.call_args_list if "progress" in str(c.args[0])]
        assert len(progress_calls) == 3
        # First progress: 1/3, last progress: 3/3
        assert progress_calls[0].args[2] == 1  # i + 1
        assert progress_calls[0].args[3] == 3  # count
        assert progress_calls[2].args[2] == 3  # i + 1

    @pytest.mark.usefixtures("_patch_event_exists")
    def test_partial_failure_with_notification(self, throttle_participants, _patch_sleep):
        """Mixed results: tracks errors and notifies organizer."""
        event_id = uuid4()
        call_count = 0

        def provision_side_effect(pk, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("CMS down")
            return {"status": "provisioning", "retries": 0}

        with (
            patch.object(CTFParticipant, "objects") as mock_part,
            patch.object(
                range_service,
                "provision_participant_range_with_retry",
                side_effect=provision_side_effect,
            ),
            patch("ctf.services.notification.notify_organizer_provision_failure") as mock_notify,
        ):
            mock_part.filter.return_value = throttle_participants

            result = range_service.provision_event_ranges_throttled(event_id, 300)

        assert result["successful"] == 2
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "CMS down" in result["errors"][0]["error"]
        mock_notify.assert_called_once()

    @pytest.mark.usefixtures("_patch_event_exists")
    def test_delay_clamping(self):
        """Delay is clamped to [5, 120] seconds."""
        participants = [Mock(pk=uuid4()) for _ in range(2)]

        # Test floor clamp: window=2s / 2 participants = 1s raw -> clamped to 5s
        # Test ceiling clamp: window=500s / 2 participants = 250s raw -> clamped to 120s
        # Test passthrough: window=100s / 2 participants = 50s raw -> 50s
        for window, expected_delay in [(2, 5.0), (500, 120.0), (100, 50.0)]:
            with (
                patch.object(CTFEvent, "objects") as mock_event,
                patch.object(CTFParticipant, "objects") as mock_part,
                patch.object(
                    range_service,
                    "provision_participant_range_with_retry",
                    return_value={"status": "provisioning", "retries": 0},
                ),
                patch("ctf.services.range.time.sleep") as mock_sleep,
            ):
                mock_event.get.return_value = Mock()
                mock_event.DoesNotExist = CTFEvent.DoesNotExist
                mock_part.filter.return_value = participants

                range_service.provision_event_ranges_throttled(uuid4(), window)

            # Two participants -> one inter-provision gap, slept in <=15s
            # heartbeat chunks whose total equals the clamped delay.
            assert sum(c.args[0] for c in mock_sleep.call_args_list) == expected_delay

    @pytest.mark.usefixtures("_patch_event_exists")
    def test_shutdown_interruption(self, _patch_sleep):
        """shutdown_check stops the loop and sets interrupted=True."""
        participants = [Mock(pk=uuid4()) for _ in range(5)]
        provisioned: list = []
        stop = {"flag": False}

        def provision_side_effect(pk, **kwargs):
            provisioned.append(pk)
            stop["flag"] = True  # request shutdown after the first provision
            return {"status": "provisioning", "retries": 0}

        def shutdown_check():
            return stop["flag"]

        with (
            patch.object(CTFParticipant, "objects") as mock_part,
            patch.object(
                range_service,
                "provision_participant_range_with_retry",
                side_effect=provision_side_effect,
            ),
        ):
            mock_part.filter.return_value = participants

            result = range_service.provision_event_ranges_throttled(uuid4(), 600, shutdown_check=shutdown_check)

        assert result["interrupted"] is True
        # Loop breaks at the top of the second iteration, so only one provision ran.
        assert len(provisioned) == 1

    @pytest.mark.django_db
    @pytest.mark.usefixtures("_patch_sleep")
    def test_heartbeat_called_each_iteration(self, ctf_event):
        """The heartbeat fires at least once per participant so the task stays live.

        DB-backed with unregistered participants: provisioning fails fast before
        any CMS/network call. The heartbeat also fires during the chunked
        inter-provision waits (#943), so the count is >= the participant count.
        """
        for i in range(3):
            _make_unregistered_participant(ctf_event, i)
        heartbeat = Mock()

        result = range_service.provision_event_ranges_throttled(ctf_event.pk, 300, heartbeat=heartbeat)

        assert heartbeat.call_count >= 3
        assert result["total"] == 3

    @pytest.mark.django_db
    @pytest.mark.usefixtures("_patch_sleep")
    def test_heartbeat_failure_does_not_abort_spin_up(self, ctf_event):
        """A failing heartbeat is swallowed; the spin-up loop still runs to completion."""
        for i in range(3):
            _make_unregistered_participant(ctf_event, i)
        heartbeat = Mock(side_effect=RuntimeError("db hiccup"))

        result = range_service.provision_event_ranges_throttled(ctf_event.pk, 300, heartbeat=heartbeat)

        assert heartbeat.call_count >= 3
        assert result["total"] == 3


class TestIsAlreadyAssignedError:
    """The benign race-loser discriminator that keeps the skip path off the failure path (CTF-7)."""

    def test_already_assigned_message_is_benign(self):
        assert range_service._is_already_assigned_error(CTFRangeError("Participant already has a range assigned"))

    def test_other_range_error_is_not_benign(self):
        assert not range_service._is_already_assigned_error(CTFRangeError("Range provisioning failed: boom"))

    def test_non_range_error_is_not_benign(self):
        assert not range_service._is_already_assigned_error(RuntimeError("boom"))


class TestInterruptibleSleep:
    """_interruptible_sleep is the keep-alive primitive used by the throttled
    loop and the retry backoff: it sleeps the full duration in chunks while
    touching the heartbeat and honoring shutdown."""

    def test_chunks_sum_to_duration_and_touch_heartbeat(self, _patch_sleep):
        heartbeat = Mock()

        range_service._interruptible_sleep(40, heartbeat=heartbeat)

        # Slept the whole 40s in <=15s chunks (15 + 15 + 10).
        assert sum(c.args[0] for c in _patch_sleep.call_args_list) == 40
        assert heartbeat.call_count >= 3

    def test_aborts_early_on_shutdown(self, _patch_sleep):
        range_service._interruptible_sleep(120, shutdown_check=lambda: True)

        # Shutdown is checked before the first chunk, so nothing is slept.
        assert _patch_sleep.call_count == 0


@pytest.mark.django_db
class TestRequestEventProvisioning:
    """request_event_provisioning enqueues/coalesces a SPIN_UP_RANGES task."""

    def _spin_up_count(self, event):
        return CTFScheduledTask.objects.filter(
            event=event,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
        ).count()

    def test_creates_due_now_task_when_none_exists(self, ctf_event):
        before = timezone.now()

        task = range_service.request_event_provisioning(ctf_event.id, source="manual")

        assert task.task_type == ScheduledTaskType.SPIN_UP_RANGES.value
        assert task.status == ScheduledTaskStatus.PENDING.value
        assert before - timedelta(seconds=2) <= task.scheduled_for <= timezone.now()
        assert task.metadata.get("source") == "manual"
        assert self._spin_up_count(ctf_event) == 1

    def test_coalesces_future_pending_pulled_to_now(self, ctf_event):
        existing = CTFScheduledTask.objects.create(
            event=ctf_event,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
            scheduled_for=timezone.now() + timedelta(hours=6),
        )

        task = range_service.request_event_provisioning(ctf_event.id, source="manual")

        assert task.pk == existing.pk
        task.refresh_from_db()
        assert task.scheduled_for <= timezone.now()
        assert task.metadata.get("source") == "manual"
        # No duplicate runnable task was created.
        assert self._spin_up_count(ctf_event) == 1

    def test_reuses_running_task(self, ctf_event):
        existing = CTFScheduledTask.objects.create(
            event=ctf_event,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
            scheduled_for=timezone.now() - timedelta(minutes=1),
            status=ScheduledTaskStatus.RUNNING.value,
        )

        task = range_service.request_event_provisioning(ctf_event.id)

        assert task.pk == existing.pk
        assert task.status == ScheduledTaskStatus.RUNNING.value
        assert self._spin_up_count(ctf_event) == 1

    def test_event_not_found_raises(self):
        with pytest.raises(CTFNotFoundError):
            range_service.request_event_provisioning(uuid4())


@pytest.mark.django_db
class TestGetProvisionProgress:
    """get_provision_progress projects participant counts plus the active task."""

    def _participant(self, event, name, range_status, range_instance_id=None):
        return CTFParticipant.objects.create(
            event=event,
            email=f"{name}@test.com",
            name=name,
            range_status=range_status,
            range_instance_id=range_instance_id,
        )

    def test_counts_and_active_task(self, ctf_event):
        self._participant(ctf_event, "a", "ready", 1)
        self._participant(ctf_event, "b", "provisioning", 2)
        self._participant(ctf_event, "c", "error")
        self._participant(ctf_event, "d", "")  # not assigned
        task = CTFScheduledTask.objects.create(
            event=ctf_event,
            task_type=ScheduledTaskType.SPIN_UP_RANGES.value,
            scheduled_for=timezone.now(),
        )

        progress = range_service.get_provision_progress(ctf_event.id)

        counts = progress["counts"]
        assert counts["total"] == 4
        assert counts["ready"] == 1
        assert counts["provisioning"] == 1
        assert counts["error"] == 1
        assert counts["not_assigned"] == 1
        assert progress["task"]["id"] == str(task.pk)
        assert progress["task"]["status"] == ScheduledTaskStatus.PENDING.value

    def test_no_active_task(self, ctf_event):
        progress = range_service.get_provision_progress(ctf_event.id)

        assert progress["task"] is None
        assert progress["counts"]["total"] == 0
