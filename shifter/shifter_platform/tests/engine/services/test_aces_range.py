"""Behavior tests for create_aces_range() (ADR-031, ACES-native path).

Drives the real engine service against a real database: the bare, self-describing
ACES ``ProvisioningSpec`` JSON is persisted as a ``Request`` + ``Range`` keyed by
``request_id`` (in the reused ``range_config`` column, with no cyberscript
persisted envelope), an ``operation_receipt`` sidecar is written, and the
provisioner ``aces-range`` provision task is dispatched. ECS is unconfigured so
dispatch is a no-op needing no boundary mock; the failure test mocks the ECS
client at the ``boto3`` boundary. Assertions are on persisted state and return
values. The cyberscript ``create_range()`` body is untouched (ADR-031-R2).
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from engine.models import Range
from engine.services import AcesRangeRef, create_aces_range
from shared.aces.provisioning_spec import (
    PROVISIONING_SPEC_CONTRACT_VERSION,
    ProvisioningImage,
    ProvisioningNetworkSpec,
    ProvisioningNodeSpec,
    ProvisioningResources,
    ProvisioningSpec,
)
from shared.cloud.exceptions import CloudTaskError
from shared.models import AcesOperationRecord

pytestmark = pytest.mark.django_db

User = get_user_model()


def make_spec_payload(request_id) -> dict:
    """Build the persisted (bare, self-describing) form of a small ProvisioningSpec."""
    spec = ProvisioningSpec(
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
    return spec.model_dump(mode="json")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="aces-range@example.com", email="aces-range@example.com")


class TestCreateAcesRange:
    @pytest.fixture(autouse=True)
    def _ecs_noop(self, settings):
        # No local provisioner and no ECS cluster: dispatch is a no-op (returns
        # None), so the created range stays PROVISIONING without a boundary mock.
        settings.LOCAL_PROVISIONER = None
        settings.ENGINE_TASK_CLUSTER = ""
        settings.ENGINE_ECS_CLUSTER_ARN = ""

    def test_returns_accepted_ref(self, user):
        request_id = uuid4()
        result = create_aces_range(
            request_id=request_id, user_id=user.id, provisioning_spec=make_spec_payload(request_id)
        )
        assert isinstance(result, AcesRangeRef)
        assert result.request_id == str(request_id)
        assert result.accepted is True
        assert result.status == Range.Status.PROVISIONING
        assert result.range_id

    def test_persists_range_with_bare_spec_and_request(self, user):
        request_id = uuid4()
        payload = make_spec_payload(request_id)
        create_aces_range(request_id=request_id, user_id=user.id, provisioning_spec=payload)

        range_obj = Range.objects.get()
        assert range_obj.user == user
        assert range_obj.cms_user_id == user.id
        assert range_obj.status == Range.Status.PROVISIONING
        assert range_obj.subnet_index is not None
        assert range_obj.subnet_index >= Range.SUBNET_INDEX_MIN
        assert str(range_obj.request.request_id) == str(request_id)
        # range_config is the bare, self-describing ProvisioningSpec JSON: it
        # carries its own contract_version discriminator, no cyberscript envelope.
        assert range_obj.range_config == payload
        assert range_obj.range_config["contract_version"] == PROVISIONING_SPEC_CONTRACT_VERSION
        assert range_obj.range_config["request_id"] == str(request_id)
        assert range_obj.range_config["nodes"][0]["os_family"] == "linux"

    def test_writes_operation_receipt_sidecar(self, user):
        request_id = uuid4()
        create_aces_range(request_id=request_id, user_id=user.id, provisioning_spec=make_spec_payload(request_id))

        receipt = AcesOperationRecord.objects.get(
            request_id=request_id, record_kind=AcesOperationRecord.RecordKind.OPERATION_RECEIPT
        )
        assert receipt.operation_id == str(request_id)
        assert receipt.payload["accepted"] is True

    def test_idempotent_on_request_id(self, user):
        request_id = uuid4()
        payload = make_spec_payload(request_id)
        first = create_aces_range(request_id=request_id, user_id=user.id, provisioning_spec=payload)
        second = create_aces_range(request_id=request_id, user_id=user.id, provisioning_spec=payload)

        assert second.range_id == first.range_id
        assert Range.objects.count() == 1


@pytest.mark.django_db
class TestCreateAcesRangeDispatchFailure:
    def test_marks_range_failed_when_dispatch_fails(self, user, settings):
        settings.CLOUD_PROVIDER = "aws"
        settings.LOCAL_PROVISIONER = None
        settings.ENGINE_TASK_CLUSTER = "test-cluster"
        settings.ENGINE_TASK_DEFINITION = "test-taskdef"
        settings.ENGINE_TASK_NETWORK_SECURITY_GROUP_ID = "sg-test"
        settings.ENGINE_TASK_NETWORK_SUBNET_IDS = "subnet-aaa,subnet-bbb"
        ecs_client = MagicMock()
        ecs_client.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]}

        request_id = uuid4()
        with patch("boto3.client", return_value=ecs_client), pytest.raises(CloudTaskError):
            create_aces_range(request_id=request_id, user_id=user.id, provisioning_spec=make_spec_payload(request_id))

        range_obj = Range.objects.get(request__request_id=request_id)
        assert range_obj.status == Range.Status.FAILED
        assert range_obj.error_message == "Provisioning dispatch failed"
