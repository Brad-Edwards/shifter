"""Behavior tests for the experiment orchestrator coordinator.

Drives ``ExperimentOrchestrator`` scheduling, completion detection, run-failure
handling, and execution-plan construction against real ``Experiment`` /
``ExperimentRun`` / ``ExperimentScript`` rows (including real
``transaction.atomic`` / ``select_for_update``, real scenario hydration, and
real ``engine.services.create_range``). ECS is left unconfigured, so range
provisioning persists its records without touching any cloud boundary — no
first-party seams are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from cms.experiments.exceptions import ExecutionPlanError
from cms.experiments.models import Experiment, ExperimentRun, ExperimentScript, ScriptAsset
from cms.experiments.orchestrator import ExperimentOrchestrator, execution_plan
from cms.experiments.schemas import ExperimentStatus, RunStatus

ARN = "arn:aws:ecs:us-east-2:123:task/abc"


@pytest.fixture
def ecs_configured(settings):
    """Configure the experiment ECS task so start_experiment_task reaches boto3."""
    settings.CLOUD_PROVIDER = "aws"
    settings.ENGINE_TASK_CLUSTER = "test-cluster"
    settings.EXPERIMENT_TASK_DEFINITION = "test-taskdef"
    settings.ENGINE_TASK_NETWORK_SECURITY_GROUP_ID = "sg-123"
    settings.ENGINE_TASK_NETWORK_SUBNET_IDS = "subnet-1,subnet-2"
    return settings


def _ecs_client():
    client = MagicMock()
    client.run_task.return_value = {"tasks": [{"taskArn": ARN}], "failures": []}
    return client


pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-orch@example.com", email="exp-orch@example.com")


@pytest.fixture
def make_experiment(user, make_agent, hydratable_scenario):
    """Create a real Experiment whose scenario hydrates with a single Windows agent."""

    def _make(*, status=ExperimentStatus.RUNNING.value, max_parallel_runs=2, total_runs=5, with_agent=True):
        return Experiment.objects.create(
            user=user,
            name="Exp",
            scenario_id=hydratable_scenario.scenario_id,
            agent=make_agent(user) if with_agent else None,
            status=status,
            max_parallel_runs=max_parallel_runs,
            total_runs=total_runs,
        )

    return _make


def _pending_runs(experiment, count, *, start=1):
    return [
        ExperimentRun.objects.create(experiment=experiment, run_number=i, status=RunStatus.PENDING.value)
        for i in range(start, start + count)
    ]


class TestScheduleRuns:
    """schedule_runs — scheduling, max_parallel, transition logic."""

    def test_transitions_queued_to_running(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.QUEUED.value, total_runs=0)

        scheduled = ExperimentOrchestrator(exp.pk).schedule_runs()

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.RUNNING.value
        assert scheduled == 0

    def test_respects_max_parallel(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value, max_parallel_runs=2)
        runs = _pending_runs(exp, 5)

        scheduled = ExperimentOrchestrator(exp.pk).schedule_runs()

        assert scheduled == 2
        provisioning = ExperimentRun.objects.filter(experiment=exp, status=RunStatus.PROVISIONING.value).count()
        pending = ExperimentRun.objects.filter(experiment=exp, status=RunStatus.PENDING.value).count()
        assert provisioning == 2
        assert pending == 3
        for run in runs[:2]:
            run.refresh_from_db()
            assert run.request_id is not None

    def test_schedules_nothing_when_full(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value, max_parallel_runs=1)
        ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)
        _pending_runs(exp, 2, start=2)

        scheduled = ExperimentOrchestrator(exp.pk).schedule_runs()

        assert scheduled == 0
        assert ExperimentRun.objects.filter(experiment=exp, status=RunStatus.PENDING.value).count() == 2

    def test_not_running_experiment_skips(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.COMPLETED.value)
        _pending_runs(exp, 2)

        scheduled = ExperimentOrchestrator(exp.pk).schedule_runs()

        assert scheduled == 0
        assert ExperimentRun.objects.filter(experiment=exp, status=RunStatus.PENDING.value).count() == 2


class TestConcurrentScheduleRuns:
    """Successive schedule_runs() calls respect slot limits independently."""

    def test_second_call_finds_no_slots(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value, max_parallel_runs=1)
        _pending_runs(exp, 2)

        orch = ExperimentOrchestrator(exp.pk)
        first = orch.schedule_runs()
        orch.refresh()
        second = orch.schedule_runs()

        assert first == 1
        assert second == 0
        assert first + second <= exp.max_parallel_runs


class TestExperimentCompletion:
    """_check_experiment_completion — terminal state detection."""

    def _runs(self, experiment, *, completed=0, failed=0, pending=0):
        n = 1
        for _ in range(completed):
            ExperimentRun.objects.create(experiment=experiment, run_number=n, status=RunStatus.COMPLETED.value)
            n += 1
        for _ in range(failed):
            ExperimentRun.objects.create(experiment=experiment, run_number=n, status=RunStatus.FAILED.value)
            n += 1
        for _ in range(pending):
            ExperimentRun.objects.create(experiment=experiment, run_number=n, status=RunStatus.PENDING.value)
            n += 1

    def test_all_completed_marks_experiment_completed(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        self._runs(exp, completed=2)

        ExperimentOrchestrator(exp.pk)._check_experiment_completion()

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.COMPLETED.value

    def test_all_failed_marks_experiment_failed(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        self._runs(exp, failed=2)

        ExperimentOrchestrator(exp.pk)._check_experiment_completion()

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.FAILED.value
        assert exp.error_message != ""

    def test_mixed_results_marks_completed(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        self._runs(exp, completed=1, failed=1)

        ExperimentOrchestrator(exp.pk)._check_experiment_completion()

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.COMPLETED.value

    def test_pending_runs_block_completion(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        self._runs(exp, completed=1, pending=1)

        ExperimentOrchestrator(exp.pk)._check_experiment_completion()

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.RUNNING.value


class TestHandleRunFailed:
    """handle_run_failed — run failure marking."""

    def test_marks_run_failed(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)

        ExperimentOrchestrator(exp.pk).handle_run_failed(run.pk, "Provisioning timed out")

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert run.error_message == "Provisioning timed out"

    def test_ignores_already_terminal(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.COMPLETED.value)

        ExperimentOrchestrator(exp.pk).handle_run_failed(run.pk, "Late failure")

        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED.value
        assert run.error_message == ""


class TestBuildExecutionPlan:
    """build_execution_plan validation and error handling."""

    def _script(self, user, experiment, *, instance_name="Workstation", s3_key="scripts/test.py", execution_order=10):
        asset = ScriptAsset.objects.create(
            user=user, name="s", s3_key=s3_key, original_filename="s.py", file_size_bytes=100
        )
        return ExperimentScript.objects.create(
            experiment=experiment,
            instance_name=instance_name,
            script_type="python",
            script=asset,
            execution_order=execution_order,
        )

    def test_raises_on_missing_instance_id(self, user, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)
        self._script(user, exp, instance_name="Workstation")

        provisioned = {"Workstation": {"hostname": "ws01"}}  # no instance_id

        with pytest.raises(ExecutionPlanError) as exc_info:
            execution_plan.build_execution_plan(exp.pk, run, provisioned)

        assert str(run.pk) in str(exc_info.value)
        assert "Workstation" in str(exc_info.value)

    def test_raises_on_missing_instance_completely(self, user, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)
        self._script(user, exp, instance_name="Workstation")

        provisioned = {"Server": {"instance_id": "i-abc123"}}

        with pytest.raises(ExecutionPlanError) as exc_info:
            execution_plan.build_execution_plan(exp.pk, run, provisioned)

        assert "Workstation" in str(exc_info.value)

    def test_builds_successfully_with_all_instances(self, user, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)
        self._script(user, exp, instance_name="Workstation")

        provisioned = {"Workstation": {"instance_id": "i-0abcdef12", "hostname": "ws01"}}

        plan = execution_plan.build_execution_plan(exp.pk, run, provisioned)

        assert plan.run_id == run.pk
        assert len(plan.victim_commands) == 1
        assert plan.victim_commands[0].instance_id == "i-0abcdef12"


def _script(user, experiment, *, instance_name, execution_order):
    asset = ScriptAsset.objects.create(
        user=user, name="s", s3_key="scripts/x.py", original_filename="x.py", file_size_bytes=100
    )
    return ExperimentScript.objects.create(
        experiment=experiment,
        instance_name=instance_name,
        script_type="python",
        script=asset,
        execution_order=execution_order,
    )


class TestHandlers:
    """Event entrypoints drive run state transitions and the ECS dispatch boundary."""

    def _run(self, experiment, *, status, metadata=None):
        from uuid import uuid4

        return ExperimentRun.objects.create(
            experiment=experiment,
            run_number=1,
            status=status,
            request_id=uuid4(),
            metadata=metadata,
        )

    def test_range_provisioned_no_scripts_completes_run(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = self._run(exp, status=RunStatus.PROVISIONING.value)

        ExperimentOrchestrator(exp.pk).handle_range_provisioned(run.pk, {})

        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED.value
        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.COMPLETED.value

    def test_range_provisioned_non_dict_payload_coerced(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = self._run(exp, status=RunStatus.PROVISIONING.value)

        # A non-dict payload is coerced to {} rather than raising.
        ExperimentOrchestrator(exp.pk).handle_range_provisioned(run.pk, ["not", "a", "dict"])

        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED.value

    def test_range_provisioned_dispatches_victims(self, user, make_experiment, ecs_configured):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        _script(user, exp, instance_name="Workstation", execution_order=10)
        run = self._run(exp, status=RunStatus.PROVISIONING.value)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            ExperimentOrchestrator(exp.pk).handle_range_provisioned(
                run.pk, {"Workstation": {"instance_id": "i-0abcdef12"}}
            )

        run.refresh_from_db()
        assert run.status == RunStatus.EXECUTING_VICTIMS.value
        assert run.metadata["dispatch_task_arn"] == ARN
        client.run_task.assert_called_once()

    def test_range_provisioned_run_not_found(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        # Must not raise on an unknown run id.
        ExperimentOrchestrator(exp.pk).handle_range_provisioned(999999, {})

    def test_victim_completed_dispatches_attacker(self, user, make_experiment, ecs_configured):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        _script(user, exp, instance_name="Attacker", execution_order=100)
        run = self._run(
            exp,
            status=RunStatus.EXECUTING_VICTIMS.value,
            metadata={"provisioned_instances": {"Attacker": {"instance_id": "i-0abcdef12"}}},
        )
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            ExperimentOrchestrator(exp.pk).handle_victim_scripts_completed(run.pk)

        run.refresh_from_db()
        assert run.status == RunStatus.EXECUTING_ATTACKER.value
        client.run_task.assert_called_once()

    def test_victim_completed_no_attacker_completes_run(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = self._run(exp, status=RunStatus.EXECUTING_VICTIMS.value, metadata={"provisioned_instances": {}})

        ExperimentOrchestrator(exp.pk).handle_victim_scripts_completed(run.pk)

        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED.value

    def test_attacker_completed_collects_artifacts(self, make_experiment, ecs_configured):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = self._run(exp, status=RunStatus.EXECUTING_ATTACKER.value, metadata={})
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            ExperimentOrchestrator(exp.pk).handle_attacker_scripts_completed(run.pk)

        run.refresh_from_db()
        assert run.status == RunStatus.COLLECTING.value
        assert run.metadata["collect_task_arn"] == ARN
        client.run_task.assert_called_once()

    def test_artifacts_collected_completes_run(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        run = self._run(exp, status=RunStatus.COLLECTING.value)

        ExperimentOrchestrator(exp.pk).handle_artifacts_collected(run.pk)

        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED.value

    def test_artifacts_collected_run_not_found(self, make_experiment):
        exp = make_experiment(status=ExperimentStatus.RUNNING.value)
        ExperimentOrchestrator(exp.pk).handle_artifacts_collected(999999)
