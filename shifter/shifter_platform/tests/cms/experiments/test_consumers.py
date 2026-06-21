"""Behavior tests for the experiment WebSocket consumer.

Drives ``ExperimentStatusConsumer`` through the real Channels
``WebsocketCommunicator`` against real ``Experiment`` / ``ExperimentRun`` rows
and real users, instead of patching the consumer's ``_get_experiment`` /
``_get_runs`` DB helpers. The broadcast-handler tests format the channel event to
the socket and assert on the consumer's ``send`` transport (the channels
boundary), which is not a first-party topology patch.
"""

import json
from unittest.mock import AsyncMock

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from cms.experiments.consumers import ExperimentStatusConsumer
from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.schemas import ExperimentStatus, RunStatus

User = get_user_model()


def _build_communicator(experiment_id, user=None):
    communicator = WebsocketCommunicator(ExperimentStatusConsumer.as_asgi(), f"/ws/experiment-status/{experiment_id}/")
    communicator.scope["url_route"] = {"kwargs": {"experiment_id": str(experiment_id)}}
    communicator.scope["user"] = user
    return communicator


@database_sync_to_async
def _make_user(username, *, is_staff=True):
    return User.objects.create_user(username=username, email=f"{username}@e.com", is_staff=is_staff)


@database_sync_to_async
def _make_experiment(owner, *, status=ExperimentStatus.DRAFT.value, runs=()):
    exp = Experiment.objects.create(user=owner, name="WS Exp", scenario_id="basic", status=status)
    for run_number, run_status in runs:
        ExperimentRun.objects.create(experiment=exp, run_number=run_number, status=run_status)
    return exp


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestConsumerAuthentication:
    async def test_rejects_anonymous_user(self):
        communicator = _build_communicator(999, user=AnonymousUser())
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4001

    async def test_rejects_no_user(self):
        communicator = _build_communicator(999, user=None)
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4001

    async def test_rejects_non_staff_user(self):
        user = await _make_user("ws-nonstaff", is_staff=False)
        communicator = _build_communicator(999, user=user)
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4003

    async def test_rejects_non_owner(self):
        owner = await _make_user("ws-owner")
        other = await _make_user("ws-other")
        exp = await _make_experiment(owner)

        communicator = _build_communicator(exp.pk, user=other)
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4004

    async def test_accepts_owner(self):
        owner = await _make_user("ws-accept")
        exp = await _make_experiment(owner)

        communicator = _build_communicator(exp.pk, user=owner)
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestConsumerHydration:
    async def test_hydrate_message_on_connect(self):
        owner = await _make_user("ws-hydrate")
        exp = await _make_experiment(
            owner,
            status=ExperimentStatus.RUNNING.value,
            runs=[(1, RunStatus.PROVISIONING.value), (2, RunStatus.PENDING.value)],
        )

        communicator = _build_communicator(exp.pk, user=owner)
        connected, _ = await communicator.connect()
        assert connected is True

        response = await communicator.receive_json_from(timeout=3)
        assert response["type"] == "hydrate"
        assert response["experiment_id"] == exp.pk
        assert response["experiment_status"] == ExperimentStatus.RUNNING.value
        assert [(r["run_number"], r["status"]) for r in response["runs"]] == [
            (1, RunStatus.PROVISIONING.value),
            (2, RunStatus.PENDING.value),
        ]
        await communicator.disconnect()

    async def test_hydrate_empty_runs(self):
        owner = await _make_user("ws-hydrate-empty")
        exp = await _make_experiment(owner, status=ExperimentStatus.DRAFT.value)

        communicator = _build_communicator(exp.pk, user=owner)
        connected, _ = await communicator.connect()
        assert connected is True

        response = await communicator.receive_json_from(timeout=3)
        assert response["type"] == "hydrate"
        assert response["runs"] == []
        await communicator.disconnect()


@pytest.mark.asyncio
class TestConsumerBroadcast:
    """The broadcast handlers format the channel event onto the socket transport."""

    async def test_receives_run_status_broadcast(self):
        consumer = ExperimentStatusConsumer()
        consumer.send = AsyncMock()

        await consumer.experiment_run_status(
            {
                "type": "experiment.run_status",
                "run_id": 42,
                "run_number": 1,
                "status": "executing_victims",
                "error_message": "",
            }
        )

        consumer.send.assert_called_once()
        response = json.loads(consumer.send.call_args.kwargs["text_data"])
        assert response["type"] == "run_status"
        assert response["run_id"] == 42
        assert response["run_number"] == 1
        assert response["status"] == "executing_victims"

    async def test_receives_experiment_status_broadcast(self):
        consumer = ExperimentStatusConsumer()
        consumer.send = AsyncMock()

        await consumer.experiment_status({"type": "experiment.status", "experiment_id": 100, "status": "completed"})

        consumer.send.assert_called_once()
        response = json.loads(consumer.send.call_args.kwargs["text_data"])
        assert response["type"] == "experiment_status"
        assert response["experiment_id"] == 100
        assert response["status"] == "completed"
