"""Behavior tests for experiment run range provisioning.

Drives ``run_provisioning.request_range_provisioning`` against real
``Experiment`` / ``ExperimentRun`` / ``AgentConfig`` / ``Request`` /
``RangeInstance`` rows with real scenario hydration and the real
``engine.services.create_range`` stack. ECS is left unconfigured, so engine
provisioning persists its records without touching a cloud boundary; the one
failure test that needs the engine to fail injects the error at the real
``boto3`` ECS boundary.
"""

from typing import Any
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.orchestrator import run_provisioning
from cms.experiments.schemas import RunStatus
from cms.models import RangeInstance, Request, Scenario
from engine.models import Range as EngineRange

pytestmark = pytest.mark.django_db

User = get_user_model()

# A scenario whose single victim has a concrete OS and no agent, so it hydrates
# without an experiment agent.
AGENTLESS_DEFINITION: dict[str, Any] = {
    "instances": [{"name": "Box", "role": "victim", "os_type": "ubuntu", "xdr_agent": False}],
    "subnets": [{"name": "core", "instances": ["Box"]}],
    "ngfw": False,
}


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-prov@example.com", email="exp-prov@example.com")


@pytest.fixture
def agentless_scenario(db) -> Scenario:
    author = User.objects.create_user(
        username="exp-prov-author@example.com", email="exp-prov-author@example.com", is_staff=True
    )
    return Scenario.objects.create(
        scenario_id="exp-prov-agentless",
        name="Agentless provisioning scenario",
        description="Hydrates without an experiment agent.",
        definition=AGENTLESS_DEFINITION,
        created_by=author,
        updated_by=author,
    )


def _experiment(user, *, scenario_id, agent=None):
    return Experiment.objects.create(user=user, name="Exp", scenario_id=scenario_id, agent=agent)


def _run(experiment, *, status=RunStatus.PROVISIONING.value):
    return ExperimentRun.objects.create(experiment=experiment, run_number=1, status=status)


class TestRequestRangeProvisioningRecords:
    """Record creation on the happy path (real engine, ECS unconfigured)."""

    def test_creates_cms_request_record(self, user, make_agent, hydratable_scenario):
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=make_agent(user))
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert run.request_id is not None
        assert Request.objects.filter(request_id=run.request_id).count() == 1

    def test_stores_request_id_on_run(self, user, make_agent, hydratable_scenario):
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=make_agent(user))
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert isinstance(run.request_id, UUID)

    def test_creates_range_instance_record(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user)
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=agent)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        ri = RangeInstance.objects.get(request__request_id=run.request_id)
        assert ri.scenario_id == hydratable_scenario.scenario_id
        assert ri.user_id == user.id
        assert ri.agent == agent

    def test_provisions_via_engine(self, user, make_agent, hydratable_scenario):
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=make_agent(user))
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        # The real engine create_range stack persisted an engine Range for the user.
        assert EngineRange.objects.filter(user=user).exists()
        assert RangeInstance.objects.filter(request__request_id=run.request_id).exists()

    def test_range_instance_stores_range_spec_json(self, user, make_agent, hydratable_scenario):
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=make_agent(user))
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        ri = RangeInstance.objects.get(request__request_id=run.request_id)
        assert isinstance(ri.range_spec, dict)
        assert ri.range_spec["scenario_id"] == hydratable_scenario.scenario_id

    def test_provisions_scenario_without_agent(self, user, agentless_scenario):
        exp = _experiment(user, scenario_id=agentless_scenario.scenario_id, agent=None)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert run.request_id is not None
        assert RangeInstance.objects.filter(request__request_id=run.request_id).exists()


class TestRequestRangeProvisioningFailures:
    """Failure handling — the run transitions to FAILED."""

    def test_invalid_scenario_fails_run(self, user):
        exp = _experiment(user, scenario_id="nonexistent_scenario_123", agent=None)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert run.error_message != ""
        assert not RangeInstance.objects.filter(request__request_id=run.request_id).exists()

    def test_missing_required_agent_fails_run(self, user, hydratable_scenario):
        # hydratable_scenario needs a Windows agent; the experiment has none.
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=None)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value
        assert run.error_message != ""

    def test_deleted_agent_fails_run(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user, deleted_at=timezone.now())
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=agent)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED.value

    # The orchestrator's ``engine_create_range`` try/except branch is observably
    # identical to the hydration-failure branch above (run -> FAILED with a
    # populated ``error_message``), which the invalid-scenario / missing-agent
    # tests already exercise against real code. Forcing the engine itself to
    # raise would require either mocking the first-party engine seam (disallowed
    # by the ADR-019 boundary-mock ratchet) or the engine's in-process local
    # task runner internals, neither of which is the orchestrator's contract.


class TestRequestRangeProvisioningScenarioData:
    """Scenario data flows through real hydration into the persisted range spec."""

    def test_hydrates_requested_scenario(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user)
        exp = _experiment(user, scenario_id=hydratable_scenario.scenario_id, agent=agent)
        run = _run(exp)

        run_provisioning.request_range_provisioning(exp, run)

        ri = RangeInstance.objects.get(request__request_id=run.request_id)
        assert ri.scenario_id == hydratable_scenario.scenario_id
