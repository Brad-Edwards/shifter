"""API-level retry-safe launch behavior (#2086, ADR-062).

Drives the real launch endpoint with an ``Idempotency-Key`` header: a replay with
the same key + selections recovers the original range without a duplicate; a
replay with different selections conflicts (409); an oversized key is rejected.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _launch(client, agent_id, scenario, key=None):
    extra = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
    return client.post(
        reverse("v1:mission_control:range-launch"),
        data=json.dumps({"agent_id": agent_id, "scenario": scenario}),
        content_type="application/json",
        **extra,
    )


def test_replay_same_key_recovers_without_duplicate(authenticated_client, make_agent, hydratable_scenario):
    from engine.models import PublicOperationRetryBinding

    client, user = authenticated_client(email="retry-api@example.com")
    agent = make_agent(user)
    scenario = hydratable_scenario.scenario_id

    first = _launch(client, agent.id, scenario, key="idem-1")
    assert first.status_code == 200, first.content
    assert first.json().get("recovered") is False

    second = _launch(client, agent.id, scenario, key="idem-1")
    assert second.status_code == 200, second.content
    assert second.json().get("recovered") is True
    # The replay must recover the ORIGINAL bound range, not a re-projected null or
    # a different active range (guards the _bound_range_response recovery path).
    assert second.json()["range"] is not None
    assert second.json()["range"]["request_id"] == first.json()["range"]["request_id"]

    assert PublicOperationRetryBinding.objects.filter(actor_key=str(user.id)).count() == 1


def test_same_key_different_selection_conflicts(authenticated_client, make_agent, hydratable_scenario):
    client, user = authenticated_client(email="retry-conflict@example.com")
    agent_one = make_agent(user)
    agent_two = make_agent(user)
    scenario = hydratable_scenario.scenario_id

    first = _launch(client, agent_one.id, scenario, key="idem-2")
    assert first.status_code == 200, first.content

    conflict = _launch(client, agent_two.id, scenario, key="idem-2")
    assert conflict.status_code == 409, conflict.content


def test_oversized_key_rejected(authenticated_client, make_agent, hydratable_scenario):
    client, user = authenticated_client(email="retry-oversized@example.com")
    agent = make_agent(user)
    scenario = hydratable_scenario.scenario_id

    response = _launch(client, agent.id, scenario, key="x" * 201)
    assert response.status_code == 400, response.content


def test_launch_without_key_is_unchanged(authenticated_client, make_agent, hydratable_scenario):
    from engine.models import PublicOperationRetryBinding

    client, user = authenticated_client(email="retry-none@example.com")
    agent = make_agent(user)
    scenario = hydratable_scenario.scenario_id

    response = _launch(client, agent.id, scenario, key=None)
    assert response.status_code == 200, response.content
    assert "recovered" not in response.json()
    assert PublicOperationRetryBinding.objects.count() == 0
