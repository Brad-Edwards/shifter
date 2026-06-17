"""Behavior tests for experiment event publishing.

Drives the real ``publish_experiment_event`` / ``publish_range_provisioned_for_experiment``
through the real ``shared.cloud`` AWS queue publisher down to the ``boto3`` SQS
client (mocked at that boundary), instead of patching ``settings`` and the
first-party ``get_queue_publisher``. Queue config is set via the ``settings``
fixture; failures are driven with a ``boto3`` ``ClientError``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from cms.experiments.events import (
    ExperimentEventError,
    publish_experiment_event,
    publish_range_provisioned_for_experiment,
)

CMS_URL = "https://sqs.us-east-2.amazonaws.com/123/cms-tasks"


@pytest.fixture
def sqs_client(settings):
    """Configure the CMS SQS queue and patch boto3 with a mock SQS client."""
    settings.CLOUD_PROVIDER = "aws"
    settings.SQS_QUEUE_CONFIG = {"cms": {"url": CMS_URL}}
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        yield client


def _sent_body(sqs_client):
    """Return the JSON-decoded MessageBody from the single send_message call."""
    sqs_client.send_message.assert_called_once()
    return json.loads(sqs_client.send_message.call_args.kwargs["MessageBody"])


class TestPublishExperimentEvent:
    def test_publishes_successfully(self, sqs_client):
        publish_experiment_event(
            event_type="experiment.run.range_provisioned", payload={"experiment_id": 1, "run_id": 1}
        )
        assert sqs_client.send_message.call_args.kwargs["QueueUrl"] == CMS_URL
        assert _sent_body(sqs_client)["event_type"] == "experiment.run.range_provisioned"

    def test_raises_when_not_configured(self, settings):
        settings.SQS_QUEUE_CONFIG = {}
        with pytest.raises(ExperimentEventError, match="publisher not configured"):
            publish_experiment_event(event_type="test.event", payload={"data": "value"})

    def test_raises_when_url_empty(self, settings):
        settings.SQS_QUEUE_CONFIG = {"cms": {"url": ""}}
        with pytest.raises(ExperimentEventError, match="publisher not configured"):
            publish_experiment_event(event_type="test.event", payload={"data": "value"})

    def test_prefers_publisher_id_when_present(self, sqs_client, settings):
        settings.SQS_QUEUE_CONFIG = {
            "cms": {
                "url": "projects/test/subscriptions/shifter-gcp-dev-cms",
                "publisher_id": "projects/test/topics/shifter-gcp-dev-events",
            }
        }
        publish_experiment_event(
            event_type="experiment.run.range_provisioned", payload={"experiment_id": 1, "run_id": 1}
        )
        assert sqs_client.send_message.call_args.kwargs["QueueUrl"] == "projects/test/topics/shifter-gcp-dev-events"

    def test_raises_on_cloud_queue_failure(self, sqs_client):
        sqs_client.send_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Service unavailable"}}, "SendMessage"
        )
        with pytest.raises(ExperimentEventError) as exc_info:
            publish_experiment_event(
                event_type="experiment.run.range_provisioned", payload={"experiment_id": 1, "run_id": 1}
            )
        assert "experiment.run.range_provisioned" in str(exc_info.value)
        from shared.cloud.exceptions import CloudQueueError

        assert isinstance(exc_info.value.__cause__, CloudQueueError)

    def test_exception_message_includes_event_type(self, sqs_client):
        sqs_client.send_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Network error"}}, "SendMessage"
        )
        with pytest.raises(ExperimentEventError, match=r"experiment\.run\.custom_event"):
            publish_experiment_event(event_type="experiment.run.custom_event", payload={"data": "test"})


class TestPublishRangeProvisioned:
    def test_publishes_with_correct_payload(self, sqs_client):
        provisioned_instances = {
            "Workstation": {"instance_id": "i-abc123"},
            "Server": {"instance_id": "i-def456"},
        }
        publish_range_provisioned_for_experiment(
            experiment_id=42, run_id=7, provisioned_instances=provisioned_instances
        )

        body = _sent_body(sqs_client)
        assert body["event_type"] == "experiment.run.range_provisioned"
        assert body["experiment_id"] == 42
        assert body["run_id"] == 7
        assert body["provisioned_instances"] == provisioned_instances

    def test_raises_on_publish_failure(self, sqs_client):
        sqs_client.send_message.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Queue failure"}}, "SendMessage"
        )
        with pytest.raises(ExperimentEventError):
            publish_range_provisioned_for_experiment(experiment_id=1, run_id=1, provisioned_instances={})
