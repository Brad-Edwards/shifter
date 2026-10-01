"""Interrupted native launches are removed only after complete ownership inventory."""

from copy import deepcopy
from unittest.mock import Mock
from uuid import UUID

import pytest

from ec2_range_cleanup import Ec2CleanupError, Ec2CleanupScope, destroy_ec2_resources, inventory_ec2_resources


def fixture():
    scope = Ec2CleanupScope("test", "us-east-1", "vpc-" + "0" * 17, UUID(int=1), UUID(int=2), 7)
    tags = [{"Key": key, "Value": value} for key, value in scope.tags().items()]
    rows = {
        "instances": [
            {"InstanceId": "i-" + "0" * 17, "VpcId": scope.vpc_id, "Tags": tags, "State": {"Name": "running"}}
        ],
        "volumes": [],
        "interfaces": [],
        "subnets": [{"SubnetId": "subnet-" + "0" * 17, "VpcId": scope.vpc_id, "Tags": tags}],
        "groups": [{"GroupId": "sg-" + "0" * 17, "VpcId": scope.vpc_id, "GroupName": "range", "Tags": tags}],
        "route_tables": [
            {
                "RouteTableId": "rtb-" + "1" * 17,
                "VpcId": scope.vpc_id,
                "Tags": tags,
                "Associations": [
                    {"SubnetId": "subnet-" + "0" * 17, "RouteTableAssociationId": "rtbassoc-test", "Main": False}
                ],
            }
        ],
    }
    ec2 = Mock()
    ec2.describe_instances.side_effect = lambda **kw: {"Reservations": [{"Instances": deepcopy(rows["instances"])}]}
    for category, operation, key in (
        ("volumes", "describe_volumes", "Volumes"),
        ("interfaces", "describe_network_interfaces", "NetworkInterfaces"),
        ("subnets", "describe_subnets", "Subnets"),
        ("groups", "describe_security_groups", "SecurityGroups"),
        ("route_tables", "describe_route_tables", "RouteTables"),
    ):
        getattr(ec2, operation).side_effect = lambda category=category, key=key, **kw: {key: deepcopy(rows[category])}
    ec2.terminate_instances.side_effect = lambda **kw: rows["instances"].clear()
    for category, operation in (
        ("subnets", "delete_subnet"),
        ("groups", "delete_security_group"),
        ("route_tables", "delete_route_table"),
    ):
        getattr(ec2, operation).side_effect = lambda category=category, **kw: rows[category].clear()
    return scope, ec2, rows


def test_destroy_waits_for_instances_and_independently_proves_empty_inventory():
    scope, ec2, rows = fixture()
    result = destroy_ec2_resources(scope, ec2)
    assert result["outcome"] == "VERIFIED_ABSENT"
    assert result["residual_categories"] == []
    ec2.get_waiter.assert_called_with("instance_terminated")
    calls = [call[0] for call in ec2.mock_calls]
    assert calls.index("get_waiter().wait") < calls.index("delete_security_group")
    ec2.disassociate_route_table.assert_called_once_with(AssociationId="rtbassoc-test")
    assert not any(rows.values())


@pytest.mark.parametrize("drift", ["generation", "vpc", "main", "foreign_association"])
def test_destroy_refuses_foreign_or_ambiguous_ownership_before_any_mutation(drift):
    scope, ec2, rows = fixture()
    if drift == "generation":
        rows["subnets"][0]["Tags"] = [
            {"Key": key, "Value": value}
            for key, value in (scope.tags() | {"shifter:generation": str(UUID(int=3))}).items()
        ]
    elif drift == "vpc":
        rows["groups"][0]["VpcId"] = "vpc-" + "f" * 17
    elif drift == "main":
        rows["route_tables"][0]["Associations"][0]["Main"] = True
    else:
        rows["route_tables"][0]["Associations"][0]["SubnetId"] = "subnet-" + "f" * 17
    with pytest.raises(Ec2CleanupError):
        destroy_ec2_resources(scope, ec2)
    ec2.terminate_instances.assert_not_called()
    ec2.delete_subnet.assert_not_called()


@pytest.mark.parametrize("failure", ["pagination", "provider_error"])
def test_inventory_errors_are_incomplete_and_never_empty_success(failure):
    scope, ec2, _ = fixture()
    ec2.describe_subnets.side_effect = RuntimeError("provider unavailable") if failure == "provider_error" else None
    ec2.describe_subnets.return_value = {"Subnets": [], "NextToken": "more"}
    assert inventory_ec2_resources(scope, ec2)["outcome"] == "INCOMPLETE"
    with pytest.raises(Ec2CleanupError):
        destroy_ec2_resources(scope, ec2)
    ec2.terminate_instances.assert_not_called()


def test_termination_timeout_does_not_remove_guest_network():
    scope, ec2, _ = fixture()
    ec2.get_waiter.return_value.wait.side_effect = RuntimeError("timeout")
    with pytest.raises(Ec2CleanupError):
        destroy_ec2_resources(scope, ec2)
    ec2.delete_subnet.assert_not_called()
    ec2.delete_security_group.assert_not_called()


def test_residual_after_delete_is_reported_instead_of_cleanup_success():
    scope, ec2, _ = fixture()
    ec2.delete_subnet.side_effect = None
    result = destroy_ec2_resources(scope, ec2)
    assert result["outcome"] == "RESIDUALS_FOUND"
    assert result["residual_categories"] == [{"category": "subnets", "count": 1}]
