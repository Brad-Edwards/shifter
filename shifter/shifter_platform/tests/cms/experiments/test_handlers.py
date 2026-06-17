"""Tests for SQS event handler dispatch."""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from cms.experiments.handlers import parse_sns_message, process_event
from cms.experiments.schemas import RunStatus


class TestParseMessage:
    def test_direct_dict(self):
        result = parse_sns_message({"event_type": "test"})
        assert result["event_type"] == "test"

    def test_json_string(self):
        result = parse_sns_message('{"event_type": "test"}')
        assert result["event_type"] == "test"

    def test_sns_envelope(self):
        envelope = {"Message": json.dumps({"event_type": "test"})}
        result = parse_sns_message(envelope)
        assert result["event_type"] == "test"


class TestProcessEvent:
    @patch("cms.experiments.handlers.ExperimentOrchestrator")
    def test_ignores_unknown_event(self, mock_orch_cls):
        """Unknown event_type → no orchestrator constructed, no handler dispatched."""
        process_event({"event_type": "unknown.event", "event_id": "123"})
        mock_orch_cls.assert_not_called()

    @patch("cms.experiments.handlers.ExperimentOrchestrator")
    def test_experiment_start_schedules_runs(self, mock_orch_cls):
        """experiment.start event creates orchestrator and calls schedule_runs."""
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        process_event(
            {
                "event_type": "experiment.start",
                "experiment_id": 42,
            }
        )

        mock_orch_cls.assert_called_once_with(42)
        mock_orch.schedule_runs.assert_called_once()

    @patch("cms.experiments.handlers.Experiment")
    @patch("cms.experiments.handlers.ExperimentRun")
    @patch("cms.experiments.handlers.ExperimentOrchestrator")
    def test_run_failed_event(self, mock_orch_cls, mock_run_model, mock_exp_model):
        """experiment.run.failed event calls handle_run_failed on orchestrator."""
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        # Mock the ExperimentRun.objects.get for broadcast (lookup at handler module level).
        mock_run = MagicMock(run_number=1, status=RunStatus.FAILED.value)
        mock_run_model.objects.get.return_value = mock_run
        mock_run_model.DoesNotExist = Exception

        # Mock Experiment.objects.get for broadcast (lookup at handler module level).
        mock_exp = MagicMock(status="failed")
        mock_exp_model.objects.get.return_value = mock_exp
        mock_exp_model.DoesNotExist = Exception

        process_event(
            {
                "event_type": "experiment.run.failed",
                "experiment_id": 10,
                "run_id": 5,
                "error_message": "SSM timeout",
            }
        )

        mock_orch_cls.assert_called_once_with(10)
        mock_orch.handle_run_failed.assert_called_once_with(5, "SSM timeout")

    @patch("cms.experiments.handlers.ExperimentOrchestrator")
    def test_string_experiment_id_ignored(self, mock_orch_cls):
        """String experiment_id fails int validation → orchestrator never built."""
        process_event(
            {
                "event_type": "experiment.start",
                "experiment_id": "not-an-int",
            }
        )
        mock_orch_cls.assert_not_called()

    @patch("cms.experiments.handlers.ExperimentOrchestrator")
    def test_string_run_id_ignored(self, mock_orch_cls):
        """String run_id should be silently ignored (not crash)."""
        process_event(
            {
                "event_type": "experiment.run.failed",
                "experiment_id": 10,
                "run_id": "not-an-int",
            }
        )
        # No exception raised -- event was silently dropped
        mock_orch_cls.assert_not_called()


@pytest.mark.django_db
class TestNotifications:
    """Notification + broadcast helpers, driven against real rows.

    These persist a ``WebSocketNotification`` for the experiment owner (the
    channel-layer fan-out is best-effort and falls back to persistence). Only the
    channel-layer transport is mocked at the ``channels`` boundary to drive the
    fallback path.
    """

    @pytest.fixture(autouse=True)
    def _registered(self):
        from cms.experiments.notifications import register_experiment_notifications
        from shared.notifications import clear_notification_registry

        clear_notification_registry()
        register_experiment_notifications()

    def _owner_and_experiment(self):
        from django.contrib.auth import get_user_model

        from cms.experiments.models import Experiment

        suffix = uuid4().hex[:8]
        user = get_user_model().objects.create_user(username=f"h-{suffix}@e.com", email=f"h-{suffix}@e.com")
        experiment = Experiment.objects.create(user=user, name="Handler Exp", scenario_id="basic")
        return user, experiment

    def test_experiment_recipient_returns_owner(self):
        from cms.experiments.handlers import _experiment_recipient_id

        user, experiment = self._owner_and_experiment()
        assert _experiment_recipient_id(experiment.pk) == user.id

    def test_experiment_recipient_missing_experiment_returns_none(self):
        from cms.experiments.handlers import _experiment_recipient_id

        assert _experiment_recipient_id(999999) is None

    def test_run_status_notification_skips_missing_recipient(self):
        from cms.experiments.handlers import _publish_run_status_notification
        from shared.models import WebSocketNotification

        _publish_run_status_notification(
            experiment_id=999999, run_id=5, run_number=1, status=RunStatus.FAILED.value, error_message="no owner"
        )
        assert not WebSocketNotification.objects.exists()

    def test_experiment_status_notification_skips_missing_recipient(self):
        from cms.experiments.handlers import _publish_experiment_status_notification
        from shared.models import WebSocketNotification

        _publish_experiment_status_notification(999999, "failed")
        assert not WebSocketNotification.objects.exists()

    def test_broadcast_run_status_persists_notification_when_channel_layer_fails(self):
        from cms.experiments.handlers import _broadcast_run_status
        from shared.models import WebSocketNotification

        user, experiment = self._owner_and_experiment()
        with patch("channels.layers.get_channel_layer", side_effect=RuntimeError("unavailable")):
            _broadcast_run_status(experiment.pk, 5, 1, RunStatus.FAILED.value, "SSM timeout")

        assert WebSocketNotification.objects.filter(recipient_id=user.id).exists()

    def test_broadcast_experiment_status_persists_notification_when_channel_layer_fails(self):
        from cms.experiments.handlers import _broadcast_experiment_status
        from shared.models import WebSocketNotification

        user, experiment = self._owner_and_experiment()
        with patch("channels.layers.get_channel_layer", side_effect=RuntimeError("unavailable")):
            _broadcast_experiment_status(experiment.pk, "failed")

        assert WebSocketNotification.objects.filter(recipient_id=user.id).exists()

    def test_broadcast_run_status_for_missing_run_is_noop(self):
        from cms.experiments.handlers import _broadcast_run_status_for
        from shared.models import WebSocketNotification

        _, experiment = self._owner_and_experiment()
        _broadcast_run_status_for(experiment.pk, 999999)  # no such run
        assert not WebSocketNotification.objects.exists()

    def test_broadcast_experiment_status_if_terminal_missing_experiment_is_noop(self):
        from cms.experiments.handlers import _broadcast_experiment_status_if_terminal
        from shared.models import WebSocketNotification

        _broadcast_experiment_status_if_terminal(999999)
        assert not WebSocketNotification.objects.exists()


class TestEventHandlers:
    def test_validate_event_ids_rejects_missing_fields(self):
        from cms.experiments.handlers import _validate_event_ids

        assert _validate_event_ids({}, "experiment.start", "experiment_id") is None

    @pytest.mark.parametrize(
        ("handler_name", "event"),
        [
            ("_handle_experiment_start", {}),
            ("_handle_range_provisioned", {"experiment_id": 10}),
            ("_handle_victim_scripts_completed", {"experiment_id": 10}),
            ("_handle_attacker_scripts_completed", {"experiment_id": 10}),
            ("_handle_artifacts_collected", {"experiment_id": 10}),
            ("_handle_run_failed", {"experiment_id": 10}),
        ],
    )
    def test_event_handlers_ignore_missing_ids(self, handler_name, event):
        from cms.experiments import handlers

        with (
            patch.object(handlers, "ExperimentOrchestrator") as mock_orchestrator,
            patch.object(handlers, "_broadcast_experiment_status") as mock_experiment_broadcast,
            patch.object(handlers, "_broadcast_run_status_for") as mock_run_broadcast,
            patch.object(handlers, "_broadcast_experiment_status_if_terminal") as mock_terminal_broadcast,
        ):
            getattr(handlers, handler_name)(event)

        mock_orchestrator.assert_not_called()
        mock_experiment_broadcast.assert_not_called()
        mock_run_broadcast.assert_not_called()
        mock_terminal_broadcast.assert_not_called()

    def test_experiment_start_handler_schedules_runs_and_broadcasts_running(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_experiment_status") as mock_broadcast,
        ):
            handlers._handle_experiment_start({"experiment_id": 10})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.schedule_runs.assert_called_once()
        mock_broadcast.assert_called_once_with(10, "running")

    def test_range_provisioned_handler_dispatches_and_broadcasts_run_status(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        instances = {"attacker": {"id": "i-1"}}
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_run_status_for") as mock_broadcast,
        ):
            handlers._handle_range_provisioned({"experiment_id": 10, "run_id": 5, "provisioned_instances": instances})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.handle_range_provisioned.assert_called_once_with(5, instances)
        mock_broadcast.assert_called_once_with(10, 5)

    def test_victim_scripts_completed_handler_dispatches_and_broadcasts_run_status(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_run_status_for") as mock_broadcast,
        ):
            handlers._handle_victim_scripts_completed({"experiment_id": 10, "run_id": 5})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.handle_victim_scripts_completed.assert_called_once_with(5)
        mock_broadcast.assert_called_once_with(10, 5)

    def test_attacker_scripts_completed_handler_dispatches_and_broadcasts_run_status(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_run_status_for") as mock_broadcast,
        ):
            handlers._handle_attacker_scripts_completed({"experiment_id": 10, "run_id": 5})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.handle_attacker_scripts_completed.assert_called_once_with(5)
        mock_broadcast.assert_called_once_with(10, 5)

    def test_artifacts_collected_handler_dispatches_and_broadcasts_statuses(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_run_status_for") as mock_run_broadcast,
            patch.object(handlers, "_broadcast_experiment_status_if_terminal") as mock_terminal_broadcast,
        ):
            handlers._handle_artifacts_collected({"experiment_id": 10, "run_id": 5})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.handle_artifacts_collected.assert_called_once_with(5)
        mock_run_broadcast.assert_called_once_with(10, 5)
        mock_terminal_broadcast.assert_called_once_with(10)

    def test_run_failed_handler_dispatches_and_broadcasts_statuses(self):
        from cms.experiments import handlers

        mock_orchestrator = MagicMock()
        with (
            patch.object(handlers, "ExperimentOrchestrator", return_value=mock_orchestrator) as mock_orchestrator_cls,
            patch.object(handlers, "_broadcast_run_status_for") as mock_run_broadcast,
            patch.object(handlers, "_broadcast_experiment_status_if_terminal") as mock_terminal_broadcast,
        ):
            handlers._handle_run_failed({"experiment_id": 10, "run_id": 5})

        mock_orchestrator_cls.assert_called_once_with(10)
        mock_orchestrator.handle_run_failed.assert_called_once_with(5, "Unknown error")
        mock_run_broadcast.assert_called_once_with(10, 5, error_message="Unknown error")
        mock_terminal_broadcast.assert_called_once_with(10)
