"""Behavior tests for experiment lifecycle services (start / cancel / get / list).

Drives ``start_experiment`` / ``cancel_experiment`` / ``get_experiment`` /
``list_experiments`` / ``get_scenario_instances`` against real ``Experiment`` /
``ExperimentRun`` / ``AuditLog`` rows and the real scenario registry, with the
``experiment.start`` SQS publish exercised through the real ``shared.cloud``
queue publisher mocked at the ``boto3`` boundary — instead of patching
``Experiment`` / ``ExperimentRun`` / ``audit_log`` / ``publish_experiment_event``
/ ``load_scenario_template`` / ``transaction`` / ``_check_result_type``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model

from cms.experiments import services
from cms.experiments.exceptions import ExperimentError, ExperimentStateError, ExperimentValidationError
from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.schemas import ExperimentCreateInput, ExperimentStatus, RunStatus
from shared.constants import USER_CANNOT_BE_NONE

pytestmark = pytest.mark.django_db

User = get_user_model()

CMS_URL = "https://sqs.us-east-2.amazonaws.com/123/cms-tasks"


@pytest.fixture
def sqs_client(settings):
    settings.CLOUD_PROVIDER = "aws"
    settings.SQS_QUEUE_CONFIG = {"cms": {"url": CMS_URL}}
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        yield client


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-life@e.com", email="exp-life@e.com", is_staff=True)


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="exp-life-other@e.com", email="exp-life-other@e.com", is_staff=True)


def _experiment(user, *, status=ExperimentStatus.DRAFT.value, total_runs=3, max_parallel_runs=1):
    return Experiment.objects.create(
        user=user,
        name="Test Exp",
        scenario_id="basic",
        status=status,
        total_runs=total_runs,
        max_parallel_runs=max_parallel_runs,
    )


class TestStartExperiment:
    def test_creates_runs_and_queues(self, user, sqs_client):
        exp = _experiment(user, total_runs=3)
        services.start_experiment(user, exp.pk)

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.QUEUED.value
        run_numbers = set(ExperimentRun.objects.filter(experiment=exp).values_list("run_number", flat=True))
        assert run_numbers == {1, 2, 3}

    def test_non_draft_raises(self, user):
        exp = _experiment(user, status=ExperimentStatus.QUEUED.value)
        with pytest.raises(ExperimentStateError, match="draft state"):
            services.start_experiment(user, exp.pk)

    def test_nonexistent_raises(self, user):
        with pytest.raises(ExperimentError, match="not found"):
            services.start_experiment(user, 999999)

    def test_publishes_start_event(self, user, sqs_client):
        exp = _experiment(user, total_runs=1)
        services.start_experiment(user, exp.pk)

        sqs_client.send_message.assert_called_once()
        body = json.loads(sqs_client.send_message.call_args.kwargs["MessageBody"])
        assert body["event_type"] == "experiment.start"
        assert body["experiment_id"] == exp.pk

    def test_continues_if_event_fails(self, user, sqs_client):
        exp = _experiment(user, total_runs=1)
        sqs_client.send_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "SQS unavailable"}}, "SendMessage"
        )
        services.start_experiment(user, exp.pk)  # best-effort publish; must not fail the start

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.QUEUED.value


class TestCancelExperiment:
    def test_cancel_queued(self, user):
        exp = _experiment(user, status=ExperimentStatus.QUEUED.value)
        services.cancel_experiment(user, exp.pk)
        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.CANCELLED.value

    def test_cancel_draft_raises(self, user):
        exp = _experiment(user, status=ExperimentStatus.DRAFT.value)
        with pytest.raises(ExperimentStateError, match="Cannot cancel"):
            services.cancel_experiment(user, exp.pk)


class TestGetExperiment:
    def test_get_own_experiment(self, user):
        exp = _experiment(user)
        assert services.get_experiment(user, exp.pk).pk == exp.pk

    def test_get_other_users_experiment_raises(self, user, other_user):
        exp = _experiment(other_user)
        with pytest.raises(ExperimentError, match="not found"):
            services.get_experiment(user, exp.pk)


class TestListExperiments:
    def test_list_returns_experiments(self, user):
        _experiment(user)
        assert services.list_experiments(user).count() == 1

    def test_annotates_run_counts(self, user):
        exp = _experiment(user, total_runs=3)
        ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.COMPLETED.value)
        ExperimentRun.objects.create(experiment=exp, run_number=2, status=RunStatus.PENDING.value)
        ExperimentRun.objects.create(experiment=exp, run_number=3, status=RunStatus.PENDING.value)

        listed = services.list_experiments(user).first()
        assert listed.total_run_count == 3
        assert listed.completed_runs == 1


class TestScenarioInstances:
    def test_basic_scenario_returns_instances(self):
        instances = services.get_scenario_instances("basic")
        assert {i["name"] for i in instances} == {"Attacker", "Workstation"}

    def test_invalid_scenario_raises(self):
        with pytest.raises(ExperimentValidationError, match="Invalid scenario"):
            services.get_scenario_instances("nonexistent_scenario_123")


class TestUserValidation:
    """Service functions reject a None user."""

    def test_list_scripts_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.list_scripts(None)

    def test_create_experiment_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.create_experiment(None, ExperimentCreateInput(name="Test", scenario_id="basic"))

    def test_start_experiment_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.start_experiment(None, 1)

    def test_get_experiment_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.get_experiment(None, 1)

    def test_list_experiments_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.list_experiments(None)

    def test_delete_script_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.delete_script(None, 1)

    def test_cancel_experiment_none_user(self):
        with pytest.raises(TypeError, match=USER_CANNOT_BE_NONE):
            services.cancel_experiment(None, 1)


class TestConcurrentStart:
    """The DRAFT-state guard prevents a second start from re-queuing the experiment."""

    def test_second_start_rejected_once_queued(self, user, sqs_client):
        exp = _experiment(user, total_runs=3)
        services.start_experiment(user, exp.pk)  # -> QUEUED + runs

        with pytest.raises(ExperimentStateError, match="draft state"):
            services.start_experiment(user, exp.pk)
