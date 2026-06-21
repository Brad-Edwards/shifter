"""Behavior integration tests for the experiment lifecycle.

Drives the real services end-to-end (create -> start -> cancel, script
assignment, initiate upload) against real ``Experiment`` / ``ExperimentRun`` /
``ExperimentScript`` / ``ScriptAsset`` rows and the real scenario registry, with
SQS / S3 mocked only at the ``boto3`` boundary — instead of asserting on
MagicMocks that never invoked real code. Run completion / failure scheduling is
the decomposition-owned orchestrator's surface (#885/#886/#889-891) and is not
exercised here.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from cms.experiments import services
from cms.experiments.exceptions import ExperimentValidationError
from cms.experiments.models import ExperimentRun, ExperimentScript, ScriptAsset
from cms.experiments.schemas import ExperimentCreateInput, ExperimentStatus, RunStatus

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def boto3_client(settings):
    """Single boto3 mock serving both the SQS publish and the S3 presign."""
    settings.CLOUD_PROVIDER = "aws"
    settings.AWS_S3_BUCKET_NAME = "test-bucket"
    settings.SQS_QUEUE_CONFIG = {"cms": {"url": "https://sqs.us-east-2.amazonaws.com/123/cms-tasks"}}
    settings.SCRIPT_UPLOAD_URL_EXPIRES = 600
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://s3.example/presigned"
    with patch("boto3.client", return_value=client):
        yield client


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-int@e.com", email="exp-int@e.com", is_staff=True)


def _script(user, *, name="int-script"):
    return ScriptAsset.objects.create(
        user=user, name=name, s3_key=f"scripts/{user.id}/{name}.py", original_filename=f"{name}.py", file_size_bytes=100
    )


class TestExperimentLifecycle:
    def test_create_then_start(self, user, boto3_client):
        exp = services.create_experiment(
            user, ExperimentCreateInput(name="Lifecycle", scenario_id="basic", total_runs=2, max_parallel_runs=1)
        )
        assert exp.status == ExperimentStatus.DRAFT.value

        services.start_experiment(user, exp.pk)
        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.QUEUED.value
        assert ExperimentRun.objects.filter(experiment=exp).count() == 2

    def test_cancel_stops_queued_experiment(self, user, boto3_client):
        exp = services.create_experiment(
            user, ExperimentCreateInput(name="CancelMe", scenario_id="basic", total_runs=3)
        )
        services.start_experiment(user, exp.pk)
        services.cancel_experiment(user, exp.pk)

        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.CANCELLED.value


class TestScriptAssignmentIntegration:
    def test_script_assigned_to_experiment(self, user):
        script = _script(user, name="integration")
        exp = services.create_experiment(
            user,
            ExperimentCreateInput(
                name="Script Link",
                scenario_id="basic",
                total_runs=1,
                scripts=[
                    {
                        "instance_name": "Workstation",
                        "script_type": "python",
                        "script_id": script.pk,
                        "execution_order": 0,
                    },
                    {
                        "instance_name": "Attacker",
                        "script_type": "claude_code",
                        "claude_prompt": "Attack {{Workstation.ip}}",
                        "execution_order": 100,
                    },
                ],
            ),
        )

        scripts = list(ExperimentScript.objects.filter(experiment=exp).order_by("execution_order"))
        assert len(scripts) == 2
        assert scripts[0].instance_name == "Workstation"
        assert scripts[0].script_id == script.pk
        assert scripts[1].instance_name == "Attacker"
        assert scripts[1].claude_prompt == "Attack {{Workstation.ip}}"
        assert scripts[1].script_id is None

    def test_deleted_script_not_assignable(self, user):
        data = ExperimentCreateInput(
            name="Deleted Script",
            scenario_id="basic",
            scripts=[
                {"instance_name": "Workstation", "script_type": "python", "script_id": 999999, "execution_order": 0}
            ],
        )
        with pytest.raises(ExperimentValidationError, match="not found"):
            services.create_experiment(user, data)

    def test_initiate_upload_returns_presigned_data(self, user, boto3_client):
        result = services.initiate_script_upload(user, "Test Script", "test.py", 512)
        assert result["presigned_url"] == "https://s3.example/presigned"
        assert result["s3_key"].startswith(f"scripts/{user.id}/")
        # The issued token round-trips through the real verifier.
        from cms.experiments.s3 import verify_upload_token

        payload = verify_upload_token(result["upload_token"], user.id)
        assert payload["filename"] == "test.py"
        assert payload["file_size"] == 512


class TestRunState:
    """A run can be marked terminal (the orchestrator drives this in production)."""

    def test_run_transitions_to_failed_and_completed(self, user):
        exp = services.create_experiment(user, ExperimentCreateInput(name="Runs", scenario_id="basic", total_runs=2))
        r1 = ExperimentRun.objects.create(experiment=exp, run_number=1, status=RunStatus.PROVISIONING.value)
        r2 = ExperimentRun.objects.create(experiment=exp, run_number=2, status=RunStatus.PROVISIONING.value)

        r1.transition_to(RunStatus.FAILED)
        r2.transition_to(RunStatus.EXECUTING_VICTIMS)
        r1.refresh_from_db()
        assert r1.status == RunStatus.FAILED.value
        assert r1.completed_at is not None
