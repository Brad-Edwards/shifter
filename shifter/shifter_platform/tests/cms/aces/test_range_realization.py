"""Behavior tests for the CMS ACES range-realization port (#1262).

``CmsRangeRealizationPort`` is the concrete implementation of
``shared.aces.runtime_target.ShifterRangeRealizationPort``: it drives the real
``hydrate_scenario`` -> ``wrap_persisted_spec`` incumbent path against a real
DB scenario/agent so ``realize()`` proves it can emit a genuinely valid
wrapped Shifter spec.

These tests assert *observable behavior* rather than patching first-party
seams (ADR-019-R1): the translation-boundary constraint from the #1262 scope
(no persistence, no live dispatch) is proven by asserting that no engine or CMS
range rows are created, not by spying on the incumbent functions.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from cms.aces.range_realization import CmsRangeRealizationPort
from cms.exceptions import CMSError
from cms.models import RangeInstance
from cms.scenarios.hydrator import hydrate_scenario
from cms.services import get_agent
from engine.models import Range, Request
from shared.aces.runtime_target import ShifterProvisioningIntent, ShifterRealizationResult
from shared.schemas import RangeSpec
from shared.schemas.persistence import validate_persisted_spec, wrap_persisted_spec

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="aces-realize@example.com", email="aces-realize@example.com")


@pytest.fixture
def windows_agent_id(user, make_agent) -> int:
    return make_agent(user).id


@pytest.fixture
def port(user, windows_agent_id) -> CmsRangeRealizationPort:
    return CmsRangeRealizationPort(user=user, agents_by_os={"windows": windows_agent_id})


def _intent(scenario_ref: str) -> ShifterProvisioningIntent:
    return ShifterProvisioningIntent(
        scenario_ref=scenario_ref,
        node_counts_by_os={"windows": 1},
        network_addresses=(),
    )


class TestRealizeProducesValidWrappedSpec:
    """``realize`` proves a supported plan emits a genuinely valid wrapped spec."""

    def test_realize_returns_ids_and_status_only(self, port, hydratable_scenario):
        result = port.realize(_intent(hydratable_scenario.scenario_id))

        assert isinstance(result, ShifterRealizationResult)
        # A real uuid4 assigned by hydrate_scenario; raises ValueError if invalid.
        uuid.UUID(result.range_uuid)
        assert result.status == "translated"

    def test_realize_emits_a_validatable_wrapped_spec(self, port, user, windows_agent_id, hydratable_scenario):
        # Primary: exercise the SUT. ``realize`` hydrates the scenario and wraps
        # the spec through the incumbent path (range_realization.py); a spec that
        # failed to wrap/validate would raise out of ``realize``, so a clean
        # ShifterRealizationResult is the observable proof that path ran.
        result = port.realize(_intent(hydratable_scenario.scenario_id))
        assert isinstance(result, ShifterRealizationResult)

        # Secondary oracle: independently hydrate the same scenario ref and
        # round-trip it through the incumbent wrap+validate, cross-verifying that
        # what ``realize`` relies on internally is a genuinely valid, persistable
        # wrapped spec -- not just "didn't raise".
        agents = {"windows": get_agent(user, windows_agent_id)}
        range_spec = hydrate_scenario(hydratable_scenario.scenario_id, user.id, agents)
        validated = validate_persisted_spec(wrap_persisted_spec("range_spec", range_spec), "range_spec")
        assert isinstance(validated, RangeSpec)
        assert validated.scenario_id == hydratable_scenario.scenario_id

    def test_realize_uses_the_launch_context_agent(self, user, hydratable_scenario, make_agent):
        # Observable proof the port resolves agents from its launch context: an
        # owned agent yields a successful realization; an unowned one raises
        # (see TestRealizePropagatesBusinessErrors).
        agent = make_agent(user, name="Launch Context Agent")
        realization_port = CmsRangeRealizationPort(user=user, agents_by_os={"windows": agent.id})

        result = realization_port.realize(_intent(hydratable_scenario.scenario_id))

        assert result.status == "translated"
        uuid.UUID(result.range_uuid)


class TestRealizeIsTranslationOnly:
    """#1262 scope: no persistence, no live dispatch -- proven by observable state."""

    def test_realize_persists_no_range_rows(self, port, hydratable_scenario):
        # If ``realize`` had dispatched through cms.services.create_range /
        # engine.services.create_range, it would have created a CMS RangeInstance
        # and engine Request/Range rows. Their absence observably proves the
        # translation-only boundary without patching any first-party seam.
        port.realize(_intent(hydratable_scenario.scenario_id))

        assert RangeInstance.objects.count() == 0
        assert Request.objects.count() == 0
        assert Range.objects.count() == 0


class TestRealizePropagatesBusinessErrors:
    """Scenario/agent lookup failures are real CMS errors, not swallowed."""

    def test_realize_raises_cms_error_for_unknown_scenario(self, port):
        with pytest.raises(CMSError):
            port.realize(_intent("does-not-exist"))

    def test_realize_raises_for_unowned_agent_id(self, user, hydratable_scenario):
        other_user = User.objects.create_user(username="other@example.com", email="other@example.com")
        realization_port = CmsRangeRealizationPort(user=other_user, agents_by_os={"windows": 999999})

        with pytest.raises(CMSError):
            realization_port.realize(_intent(hydratable_scenario.scenario_id))
