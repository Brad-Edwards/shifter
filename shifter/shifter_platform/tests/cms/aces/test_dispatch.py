"""Behavior tests for CmsAcesDispatchPort (ADR-031, ADR-024).

The concrete dispatch port hands a neutral ``ProvisioningSpec`` to the engine
ACES range service against a real database. ECS is unconfigured so dispatch is a
no-op; assertions are on the returned ``ShifterDispatchResult`` and the persisted
``Range``. The port imports only ``shared`` + the public ``engine.services``
facade (no ``aces_*`` packages, no cyberscript), keeping the SDL tooling confined
to ``shared.aces`` (ADR-024, ADR-031-R1). The persisted ``range_config`` is the
bare, self-describing ProvisioningSpec JSON (no cyberscript persisted envelope).
"""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from cms.aces.dispatch import CmsAcesDispatchPort
from engine.models import Range
from shared.aces.dispatch_port import ShifterDispatchResult, ShifterProvisioningDispatchPort
from shared.aces.provisioning_spec import (
    PROVISIONING_SPEC_CONTRACT_VERSION,
    ProvisioningImage,
    ProvisioningNetworkSpec,
    ProvisioningNodeSpec,
    ProvisioningResources,
    ProvisioningSpec,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def make_spec(request_id) -> ProvisioningSpec:
    return ProvisioningSpec(
        request_id=str(request_id),
        nodes=(
            ProvisioningNodeSpec(
                address="attacker",
                name="attacker",
                os_family="linux",
                resources=ProvisioningResources(ram_mib=2048, vcpus=2),
                image=ProvisioningImage(name="kali", version="2024.1"),
                network_addresses=("net0",),
            ),
        ),
        networks=(ProvisioningNetworkSpec(address="net0", name="default", cidr="10.0.0.0/24"),),
    )


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cms-aces@example.com", email="cms-aces@example.com")


class TestCmsAcesDispatchPort:
    @pytest.fixture(autouse=True)
    def _ecs_noop(self, settings):
        # Dispatch is a no-op (no local provisioner, no ECS cluster) so realize()
        # accepts + persists the range without a cloud boundary mock.
        settings.LOCAL_PROVISIONER = None
        settings.ENGINE_TASK_CLUSTER = ""
        settings.ENGINE_ECS_CLUSTER_ARN = ""

    def test_satisfies_dispatch_port_protocol(self, user):
        port = CmsAcesDispatchPort(user_id=user.id, request_id=str(uuid4()))
        assert isinstance(port, ShifterProvisioningDispatchPort)

    def test_realize_returns_accepted_result(self, user):
        request_id = uuid4()
        port = CmsAcesDispatchPort(user_id=user.id, request_id=str(request_id))

        result = port.realize(make_spec(request_id))

        assert isinstance(result, ShifterDispatchResult)
        assert result.request_id == str(request_id)
        assert result.accepted is True
        assert result.status == Range.Status.PROVISIONING
        assert result.range_id

    def test_realize_persists_range_with_bare_spec(self, user):
        request_id = uuid4()
        port = CmsAcesDispatchPort(user_id=user.id, request_id=str(request_id))

        result = port.realize(make_spec(request_id))

        range_obj = Range.objects.get()
        assert str(range_obj.uuid) == result.range_id
        assert range_obj.user == user
        assert range_obj.status == Range.Status.PROVISIONING
        # Bare, self-describing ProvisioningSpec JSON (no cyberscript envelope).
        assert range_obj.range_config["contract_version"] == PROVISIONING_SPEC_CONTRACT_VERSION
        assert range_obj.range_config["request_id"] == str(request_id)
        assert range_obj.range_config["nodes"][0]["image"]["name"] == "kali"
