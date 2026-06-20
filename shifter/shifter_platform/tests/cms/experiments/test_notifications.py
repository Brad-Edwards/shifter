"""Tests for experiment notification registration and publishing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cms.experiments.notifications import (
    _experiment_id_from_topic,
    _experiment_status_payload,
    _run_status_payload,
    experiment_topic,
    publish_experiment_run_status_notification,
    register_experiment_notifications,
)


@pytest.fixture(autouse=True)
def clear_notification_registry():
    """Keep notification registrations isolated between tests."""
    from shared.notifications import clear_notification_registry

    clear_notification_registry()
    yield
    clear_notification_registry()


@pytest.mark.django_db
def test_register_experiment_notifications_authorizes_staff_owner() -> None:
    """Experiment topics use the existing staff-owner access rule (real Experiment row)."""
    from django.contrib.auth import get_user_model

    from cms.experiments.models import Experiment
    from shared.notifications import authorize_subscription

    register_experiment_notifications()
    owner = get_user_model().objects.create_user(username="exp-owner@e.com", email="exp-owner@e.com", is_staff=True)
    other_staff = get_user_model().objects.create_user(
        username="exp-other@e.com", email="exp-other@e.com", is_staff=True
    )
    experiment = Experiment.objects.create(user=owner, name="Owned", scenario_id="basic")

    # The owner is authorized; a different staff user (non-owner) is not.
    assert authorize_subscription(owner, experiment_topic(experiment.pk)) is True
    assert authorize_subscription(other_staff, experiment_topic(experiment.pk)) is False


def test_register_experiment_notifications_rejects_non_staff() -> None:
    """Experiment topics remain staff-only like the existing status socket."""
    register_experiment_notifications()
    non_staff = MagicMock(id=7, is_staff=False, is_authenticated=True)

    from shared.notifications import authorize_subscription

    assert authorize_subscription(non_staff, experiment_topic(100)) is False


def test_register_experiment_notifications_rejects_invalid_topics() -> None:
    """Experiment subscription authorization rejects malformed experiment topics."""
    register_experiment_notifications()
    staff_owner = MagicMock(id=7, is_staff=True, is_authenticated=True)

    from shared.notifications import authorize_subscription

    assert authorize_subscription(staff_owner, "range:100") is False
    assert authorize_subscription(staff_owner, "experiment:not-int") is False
    assert authorize_subscription(staff_owner, "experiment:0") is False


def test_experiment_id_from_topic_rejects_non_experiment_topic() -> None:
    """Topic parsing only accepts experiment notification topics."""
    assert _experiment_id_from_topic("range:100") is None


def test_notification_payload_projectors_return_browser_safe_fields() -> None:
    """Registered payload handlers strip unneeded source fields."""
    assert _run_status_payload(
        {
            "experiment_id": 100,
            "run_id": 5,
            "run_number": 2,
            "status": "completed",
            "unsafe": "drop",
        }
    ) == {
        "experiment_id": 100,
        "run_id": 5,
        "run_number": 2,
        "status": "completed",
        "error_message": "",
    }
    assert _experiment_status_payload(
        {
            "experiment_id": 100,
            "status": "failed",
            "unsafe": "drop",
        }
    ) == {
        "experiment_id": 100,
        "status": "failed",
    }


@pytest.mark.django_db
def test_publish_experiment_run_status_notification_persists_safe_payload(settings) -> None:
    """Publishing persists a browser-safe WebSocketNotification for the recipient/topic."""
    from django.contrib.auth import get_user_model

    from shared.models import WebSocketNotification

    settings.WEBSOCKET_NOTIFICATIONS_ENABLED = True
    register_experiment_notifications()
    recipient = get_user_model().objects.create_user(username="notif-rcpt@e.com", email="notif-rcpt@e.com")
    publish_experiment_run_status_notification(
        experiment_id=100,
        recipient_id=recipient.id,
        run_id=5,
        run_number=2,
        status="completed",
        error_message="",
    )

    notif = WebSocketNotification.objects.get(recipient_id=recipient.id, topic=experiment_topic(100))
    assert notif.notification_type == "experiment.run_status"
    assert notif.payload == {
        "experiment_id": 100,
        "run_id": 5,
        "run_number": 2,
        "status": "completed",
        "error_message": "",
    }
