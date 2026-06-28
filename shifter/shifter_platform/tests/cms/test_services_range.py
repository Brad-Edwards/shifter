"""Behavior tests for CMS range services (list_ranges, get_range, create_range).

Drives the real services against real ``RangeInstance`` rows and the full
hydrate -> engine -> persist stack (engine ECS is unconfigured in the test
settings, so provisioning is a no-op and no cloud mock is needed), instead of
patching ``RangeInstance.objects`` / the engine call / the scenario loader.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from cms import services
from cms.exceptions import CMSError
from cms.models import RangeInstance
from shared.cloud.exceptions import CloudTaskError
from shared.enums import ResourceStatus
from tests.conftest import INVALID_RANGE_IDS, INVALID_USERS

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cms-range@example.com", email="cms-range@example.com")


def _range_instance(user, *, range_id=None, scenario_id="basic", status="provisioning", agent=None):
    return RangeInstance.objects.create(
        scenario_id=scenario_id, user_id=user.id, range_id=range_id, status=status, agent=agent
    )


class TestListRanges:
    def test_returns_empty_when_user_has_no_ranges(self, user):
        assert services.list_ranges(user) == []

    def test_returns_the_users_ranges(self, user):
        _range_instance(user, range_id=1)
        _range_instance(user, range_id=2, scenario_id="ad_attack_lab")
        result = services.list_ranges(user)
        assert {r.range_id for r in result} == {1, 2}

    def test_excludes_other_users_ranges(self, user, django_user_model):
        other = django_user_model.objects.create_user(username="cms-other@e.com", email="cms-other@e.com")
        _range_instance(user, range_id=1)
        _range_instance(other, range_id=2)
        result = services.list_ranges(user)
        assert [r.range_id for r in result] == [1]

    def test_returns_a_list(self, user):
        _range_instance(user, range_id=1)
        assert type(services.list_ranges(user)) is list

    def test_requires_user_argument(self):
        with pytest.raises(TypeError):
            services.list_ranges()

    @pytest.mark.parametrize("invalid_user", INVALID_USERS)
    def test_raises_on_invalid_user(self, invalid_user):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            services.list_ranges(invalid_user)


class TestGetRange:
    def test_returns_range_when_found_and_owned(self, user):
        ri = _range_instance(user, range_id=42, scenario_id="basic")
        result = services.get_range(user, 42)
        assert result.range_id == 42
        assert result.scenario_id == "basic"
        assert result.pk == ri.pk

    def test_raises_cms_error_when_range_not_found(self, user):
        with pytest.raises(CMSError, match=r"not found|does not exist"):
            services.get_range(user, 999)

    def test_raises_cms_error_when_range_owned_by_other_user(self, user, django_user_model):
        other = django_user_model.objects.create_user(username="cms-other2@e.com", email="cms-other2@e.com")
        _range_instance(other, range_id=77)
        with pytest.raises(CMSError, match=r"not found|access denied|permission"):
            services.get_range(user, 77)

    def test_requires_user_argument(self):
        with pytest.raises(TypeError):
            services.get_range(range_id=42)

    @pytest.mark.parametrize("invalid_user", INVALID_USERS)
    def test_raises_on_invalid_user(self, invalid_user):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            services.get_range(invalid_user, 42)

    def test_requires_range_id_argument(self, user):
        with pytest.raises(TypeError):
            services.get_range(user)

    @pytest.mark.parametrize("invalid_range_id", INVALID_RANGE_IDS)
    def test_raises_on_invalid_range_id(self, user, invalid_range_id):
        with pytest.raises((TypeError, ValueError)):
            services.get_range(user, invalid_range_id)


class TestCreateRangeValidation:
    def test_raises_for_unknown_scenario(self, user, make_agent):
        agent = make_agent(user)
        with pytest.raises(CMSError, match=r"not found|scenario"):
            services.create_range(user, "nonexistent_scenario", {"windows": agent.id})

    def test_raises_when_agent_not_found(self, user, hydratable_scenario):
        with pytest.raises(CMSError, match=r"not found"):
            services.create_range(user, hydratable_scenario.scenario_id, {"windows": 999999})

    def test_raises_when_user_already_has_active_range(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user)
        services.create_range(user, hydratable_scenario.scenario_id, {"windows": agent.id})
        with pytest.raises(CMSError, match="already have an active range"):
            services.create_range(user, hydratable_scenario.scenario_id, {"windows": agent.id})


class TestCreateRangeBehavior:
    def test_creates_engine_range_in_provisioning(self, user, make_agent, hydratable_scenario):
        from engine.models import Range as EngineRange

        services.create_range(user, hydratable_scenario.scenario_id, {"windows": make_agent(user).id})
        # The engine side persisted a real Range for this user in PROVISIONING.
        eng = EngineRange.objects.filter(user=user).first()
        assert eng is not None
        assert eng.status == EngineRange.Status.PROVISIONING

    def test_persists_a_range_instance_record(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user)
        services.create_range(user, hydratable_scenario.scenario_id, {"windows": agent.id})
        ri = RangeInstance.objects.get(user_id=user.id)
        assert ri.scenario_id == hydratable_scenario.scenario_id
        assert ri.agent_id == agent.id

    def test_records_an_audit_row(self, user, make_agent, hydratable_scenario):
        from risk_register.models import AuditLog

        before = AuditLog.objects.count()
        services.create_range(user, hydratable_scenario.scenario_id, {"windows": make_agent(user).id})
        assert AuditLog.objects.count() > before

    def test_marks_owned_range_failed_when_engine_dispatch_fails(self, user, make_agent, hydratable_scenario, settings):
        from engine.models import Range as EngineRange
        from risk_register.models import AuditLog

        settings.CLOUD_PROVIDER = "aws"
        settings.LOCAL_PROVISIONER = None
        settings.ENGINE_TASK_CLUSTER = "test-cluster"
        settings.ENGINE_TASK_DEFINITION = "test-taskdef"
        settings.ENGINE_TASK_NETWORK_SECURITY_GROUP_ID = "sg-test"
        settings.ENGINE_TASK_NETWORK_SUBNET_IDS = "subnet-aaa,subnet-bbb"
        ecs_client = MagicMock()
        ecs_client.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]}

        with patch("boto3.client", return_value=ecs_client), pytest.raises(CloudTaskError):
            services.create_range(user, hydratable_scenario.scenario_id, {"windows": make_agent(user).id})

        range_instance = RangeInstance.all_objects.get(user_id=user.id)
        assert range_instance.status == ResourceStatus.FAILED.value
        assert range_instance.deleted_at is not None
        assert EngineRange.objects.get(user=user).status == EngineRange.Status.FAILED
        assert not AuditLog.objects.filter(
            entity_type=AuditLog.EntityType.RANGE,
            action=AuditLog.Action.PROVISION,
            actor_id=user.id,
        ).exists()


class TestCreateRangeReturn:
    @pytest.fixture
    def created(self, user, make_agent, hydratable_scenario):
        agent = make_agent(user, name="Windows Agent")
        ctx = services.create_range(user, hydratable_scenario.scenario_id, {"windows": agent.id})
        return ctx, agent

    def test_returns_range_context(self, created):
        from shared.schemas.range import RangeContext

        ctx, _ = created
        assert isinstance(ctx, RangeContext)

    def test_range_context_has_request_id_and_no_range_id(self, created):
        ctx, _ = created
        assert ctx.request_id is not None
        assert ctx.range_id is None

    def test_range_context_scenario_and_user(self, created, user, hydratable_scenario):
        ctx, _ = created
        assert ctx.scenario_id == hydratable_scenario.scenario_id
        assert ctx.user_id == user.id

    def test_range_context_agent_name(self, created):
        ctx, agent = created
        assert ctx.agent_name == agent.name

    def test_range_context_status_is_provisioning(self, created):
        from shared.enums import ResourceStatus

        ctx, _ = created
        assert ctx.status == ResourceStatus.PROVISIONING

    def test_range_context_instances(self, created):
        ctx, _ = created
        assert len(ctx.instances) == 2
        roles = [i.role for i in ctx.instances]
        assert "attacker" in roles
        assert "victim" in roles
        for instance in ctx.instances:
            assert instance.uuid is not None


class TestHasReadyActiveRange:
    """The cheap sidebar indicator used by the `nav` context tier (#898).

    It must mirror ``get_active_range``'s ``has_active_range`` semantics
    (latest non-DESTROYING range is READY) without building the full payload.
    """

    def test_false_when_user_has_no_range(self, user):
        assert services.has_ready_active_range(user) is False

    def test_true_when_latest_range_is_ready(self, user):
        _range_instance(user, range_id=1, status="ready")
        assert services.has_ready_active_range(user) is True

    def test_false_when_latest_range_not_ready(self, user):
        _range_instance(user, range_id=1, status="provisioning")
        assert services.has_ready_active_range(user) is False

    def test_excludes_destroying_range(self, user):
        _range_instance(user, range_id=1, status="destroying")
        assert services.has_ready_active_range(user) is False

    def test_uses_most_recent_range(self, user):
        # Older ready range, newer provisioning range -> mirrors get_active_range's
        # "most recently created" selection, so the indicator is False.
        _range_instance(user, range_id=1, status="ready")
        _range_instance(user, range_id=2, status="provisioning")
        assert services.has_ready_active_range(user) is False

    def test_excludes_other_users_range(self, user, django_user_model):
        other = django_user_model.objects.create_user(username="hr-other@e.com", email="hr-other@e.com")
        _range_instance(other, range_id=1, status="ready")
        assert services.has_ready_active_range(user) is False

    def test_requires_user_argument(self):
        with pytest.raises(TypeError):
            services.has_ready_active_range()

    @pytest.mark.parametrize("invalid_user", INVALID_USERS)
    def test_raises_on_invalid_user(self, invalid_user):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            services.has_ready_active_range(invalid_user)

    def test_is_cheaper_than_full_projection(self, user):
        """The indicator issues a single lightweight query and never resolves
        runtime IPs, FK joins, or instance contexts the way get_active_range does."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _range_instance(user, range_id=1, status="ready")
        with CaptureQueriesContext(connection) as ctx:
            services.has_ready_active_range(user)
        assert len(ctx.captured_queries) == 1
