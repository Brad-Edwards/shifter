"""Behavior tests for the range-to-experiment event bridge.

When a range becomes READY and is linked to an experiment run, the CMS handler
publishes an ``experiment.run.range_provisioned`` event to continue execution.
These tests drive the real ``notify_experiment_on_range_ready`` /
``process_range_event`` against real ``RangeInstance``/``Request``/``Experiment``/
``ExperimentRun`` rows, with the SQS publish exercised through the real
``shared.cloud`` queue publisher mocked only at the ``boto3`` boundary — instead
of patching ``ExperimentRun`` / ``publish_range_provisioned_for_experiment`` /
``RangeInstance`` / the bridge functions.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model

from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.schemas import RunStatus
from cms.handlers import notify_experiment_on_range_ready, process_range_event
from cms.models import RangeInstance, Request
from shared.enums import RequestType, ResourceStatus

pytestmark = pytest.mark.django_db

User = get_user_model()

CMS_URL = "https://sqs.us-east-2.amazonaws.com/123/cms-tasks"


@pytest.fixture
def sqs_client(settings):
    """Configure the CMS SQS queue and patch boto3 with a mock SQS client."""
    settings.CLOUD_PROVIDER = "aws"
    settings.SQS_QUEUE_CONFIG = {"cms": {"url": CMS_URL}}
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        yield client


@pytest.fixture
def user(db):
    return User.objects.create_user(username="bridge@example.com", email="bridge@example.com")


def _request(user):
    return Request.objects.create(request_id=uuid4(), request_type=RequestType.RANGE.value, user=user)


def _range_instance(user, *, request=None, range_id=None, status=ResourceStatus.PROVISIONING.value):
    return RangeInstance.objects.create(
        user_id=user.id, request=request, range_id=range_id, status=status, scenario_id="basic"
    )


def _experiment_run(user, request, *, status=RunStatus.PENDING.value):
    experiment = Experiment.objects.create(user=user, name="Exp", scenario_id="basic")
    return ExperimentRun.objects.create(
        experiment=experiment, run_number=1, request_id=request.request_id, status=status
    )


def _sent_body(sqs_client):
    sqs_client.send_message.assert_called_once()
    return json.loads(sqs_client.send_message.call_args.kwargs["MessageBody"])


class TestNotifyExperimentOnRangeReady:
    def test_publishes_event_when_linked_to_experiment(self, user, sqs_client):
        req = _request(user)
        run = _experiment_run(user, req)
        ri = _range_instance(user, request=req)

        provisioned = {"Workstation": {"instance_id": "i-abc123"}}
        notify_experiment_on_range_ready(ri, provisioned)

        body = _sent_body(sqs_client)
        assert body["event_type"] == "experiment.run.range_provisioned"
        assert body["experiment_id"] == run.experiment_id
        assert body["run_id"] == run.pk
        assert body["provisioned_instances"] == provisioned

    def test_does_nothing_for_range_without_experiment(self, user, sqs_client):
        req = _request(user)  # no ExperimentRun links to this request
        ri = _range_instance(user, request=req)
        notify_experiment_on_range_ready(ri, {})
        sqs_client.send_message.assert_not_called()

    def test_handles_missing_request_gracefully(self, user, sqs_client):
        ri = _range_instance(user, request=None)
        notify_experiment_on_range_ready(ri, {})  # must not raise
        sqs_client.send_message.assert_not_called()

    def test_marks_run_failed_on_sqs_error(self, user, sqs_client):
        req = _request(user)
        run = _experiment_run(user, req)
        ri = _range_instance(user, request=req)
        sqs_client.send_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "SQS unavailable"}}, "SendMessage"
        )

        notify_experiment_on_range_ready(ri, {"Workstation": {"instance_id": "i-abc123"}})

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert run.error_message == "Failed to publish range provisioning notification"


class TestProcessRangeEventBridgeIntegration:
    def _event(self, req, user, *, new_status, **extra):
        return {
            "event_type": "range.status.updated",
            "request_id": str(req.request_id),
            "range_id": 1,
            "user_id": user.id,
            "new_status": new_status,
            **extra,
        }

    def test_calls_bridge_on_ready(self, user, sqs_client):
        req = _request(user)
        run = _experiment_run(user, req)
        _range_instance(user, request=req, range_id=None)

        provisioned = {"Workstation": {"instance_id": "i-abc123"}}
        process_range_event(self._event(req, user, new_status=ResourceStatus.READY.value, instances=provisioned))

        body = _sent_body(sqs_client)
        assert body["experiment_id"] == run.experiment_id
        assert body["run_id"] == run.pk
        assert body["provisioned_instances"] == provisioned

    def test_no_bridge_on_non_ready(self, user, sqs_client):
        req = _request(user)
        _experiment_run(user, req)
        _range_instance(user, request=req)

        process_range_event(self._event(req, user, new_status=ResourceStatus.PROVISIONING.value))
        sqs_client.send_message.assert_not_called()
