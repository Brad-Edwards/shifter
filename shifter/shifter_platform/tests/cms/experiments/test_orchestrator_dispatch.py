"""Behavior tests for experiment command dispatch and artifact collection.

Drives ``run_dispatch.dispatch_commands`` and ``run_artifacts.collect_artifacts``
against real ``Experiment`` / ``ExperimentRun`` rows through the real
``cms.experiments.ecs.start_experiment_task`` → ``shared.cloud`` task-runner
stack. The only mocked seam is the real cloud boundary ``boto3.client`` (ECS).
Payload assembly, idempotency metadata, and run state transitions all run for
real and are asserted on the database or the ECS RunTask call.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model

from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.orchestrator import run_artifacts, run_dispatch
from cms.experiments.orchestrator.execution_plan import ScriptCommand
from cms.experiments.schemas import RunStatus

pytestmark = pytest.mark.django_db

User = get_user_model()

ARN = "arn:aws:ecs:us-east-2:123:task/abc"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-dispatch@example.com", email="exp-dispatch@example.com")


@pytest.fixture
def ecs_configured(settings):
    """Configure the experiment ECS task so start_experiment_task reaches boto3."""
    settings.CLOUD_PROVIDER = "aws"
    settings.ENGINE_TASK_CLUSTER = "test-cluster"
    settings.EXPERIMENT_TASK_DEFINITION = "test-taskdef"
    settings.ENGINE_TASK_NETWORK_SECURITY_GROUP_ID = "sg-123"
    settings.ENGINE_TASK_NETWORK_SUBNET_IDS = "subnet-1,subnet-2"
    return settings


def _ecs_client(*, task_arn=ARN, error=None):
    client = MagicMock()
    if error is not None:
        client.run_task.side_effect = error
    else:
        client.run_task.return_value = {"tasks": [{"taskArn": task_arn}], "failures": []}
    return client


def _run(user, *, status=RunStatus.EXECUTING_VICTIMS.value, request_id="set", metadata=None):
    exp = Experiment.objects.create(user=user, name="Exp", scenario_id="basic")
    return exp, ExperimentRun.objects.create(
        experiment=exp,
        run_number=1,
        status=status,
        request_id=uuid4() if request_id == "set" else request_id,
        metadata=metadata,
    )


def _container_command(client):
    kwargs = client.run_task.call_args.kwargs
    return kwargs["overrides"]["containerOverrides"][0]["command"]


def _container_env(client):
    kwargs = client.run_task.call_args.kwargs
    return {e["name"]: e["value"] for e in kwargs["overrides"]["containerOverrides"][0].get("environment", [])}


def _sample_commands():
    return [
        ScriptCommand(
            instance_name="Workstation",
            instance_id="i-abc123",
            script_type="python",
            command="python3 /tmp/script.py",
            execution_order=1,
            script_s3_key="scripts/test.py",
        ),
        ScriptCommand(
            instance_name="Workstation",
            instance_id="i-abc123",
            script_type="claude_code",
            command="claude -p 'Run nmap scan'",
            execution_order=2,
        ),
    ]


class TestDispatchCommands:
    def test_starts_ecs_task_with_execute_command(self, user, ecs_configured):
        exp, run = _run(user)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        client.run_task.assert_called_once()
        assert "execute" in _container_command(client)

    def test_passes_experiment_and_run_context(self, user, ecs_configured):
        exp, run = _run(user)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        command = _container_command(client)
        assert str(exp.pk) in command
        assert str(run.pk) in command
        assert str(run.request_id) in command

    def test_serializes_commands_in_payload(self, user, ecs_configured):
        exp, run = _run(user)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        payload = json.loads(_container_env(client)["EXPERIMENT_PAYLOAD"])
        assert payload["ai_execution_policy"]["version"] == "ai-experiment-execution-v1"
        assert len(payload["commands"]) == 2
        assert payload["commands"][0]["instance_id"] == "i-abc123"
        assert payload["commands"][0]["execution_order"] == 1

    def test_ecs_not_configured_fails_run(self, user):
        # No ecs_configured fixture: start_experiment_task returns None.
        exp, run = _run(user)

        run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "ECS" in run.error_message

    def test_ecs_failure_fails_run(self, user, ecs_configured):
        exp, run = _run(user)
        client = _ecs_client(error=ClientError({"Error": {"Code": "500", "Message": "boom"}}, "RunTask"))

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "ECS" in run.error_message

    def test_stores_task_arn_in_metadata(self, user, ecs_configured):
        exp, run = _run(user, metadata={})
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        run.refresh_from_db()
        assert run.metadata["dispatch_task_arn"] == ARN

    def test_missing_request_id_fails_run(self, user, ecs_configured):
        exp, run = _run(user, request_id=None)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "request_id" in run.error_message
        client.run_task.assert_not_called()

    def test_idempotent_when_already_dispatched(self, user, ecs_configured):
        exp, run = _run(user, metadata={"dispatch_task_arn": "arn:aws:ecs:us-east-2:123:task/existing"})
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_dispatch.dispatch_commands(exp.pk, run, _sample_commands())

        client.run_task.assert_not_called()


class TestCollectArtifacts:
    def test_starts_ecs_task_with_collect_command(self, user, ecs_configured):
        exp, run = _run(user, status=RunStatus.COLLECTING.value)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_artifacts.collect_artifacts(exp.pk, run)

        client.run_task.assert_called_once()
        assert "collect" in _container_command(client)

    def test_passes_experiment_and_run_context(self, user, ecs_configured):
        exp, run = _run(user, status=RunStatus.COLLECTING.value)
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_artifacts.collect_artifacts(exp.pk, run)

        command = _container_command(client)
        assert str(exp.pk) in command
        assert str(run.pk) in command
        assert str(run.request_id) in command

    def test_ecs_not_configured_fails_run(self, user):
        exp, run = _run(user, status=RunStatus.COLLECTING.value)

        run_artifacts.collect_artifacts(exp.pk, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "ECS" in run.error_message

    def test_ecs_failure_fails_run(self, user, ecs_configured):
        exp, run = _run(user, status=RunStatus.COLLECTING.value)
        client = _ecs_client(error=ClientError({"Error": {"Code": "500", "Message": "boom"}}, "RunTask"))

        with patch("boto3.client", return_value=client):
            run_artifacts.collect_artifacts(exp.pk, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "ECS" in run.error_message

    def test_stores_task_arn_in_metadata(self, user, ecs_configured):
        exp, run = _run(user, status=RunStatus.COLLECTING.value, metadata={})
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_artifacts.collect_artifacts(exp.pk, run)

        run.refresh_from_db()
        assert run.metadata["collect_task_arn"] == ARN

    def test_missing_request_id_fails_run(self, user):
        exp, run = _run(user, status=RunStatus.COLLECTING.value, request_id=None)

        run_artifacts.collect_artifacts(exp.pk, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert "request_id" in run.error_message

    def test_idempotent_when_already_collecting(self, user, ecs_configured):
        exp, run = _run(
            user,
            status=RunStatus.COLLECTING.value,
            metadata={"collect_task_arn": "arn:aws:ecs:us-east-2:123:task/existing"},
        )
        client = _ecs_client()

        with patch("boto3.client", return_value=client):
            run_artifacts.collect_artifacts(exp.pk, run)

        client.run_task.assert_not_called()
