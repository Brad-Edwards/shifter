"""Trusted EKS runtime-plugin pool labeler (#2526)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from shared.cloud.aws.node_pool_labeler import (
    POOL_LABEL,
    POOL_VALUE,
    InstanceRoleResolver,
    reconcile_all,
)

POOL_ROLE = "arn:aws:iam::111122223333:role/shifter-dev-runtime-plugin-node"
PLATFORM_ROLE = "arn:aws:iam::111122223333:role/shifter-dev-node"
NODE = "ip-10-0-1-10.us-east-2.compute.internal"
PLATFORM_NODE = "ip-10-0-1-11.us-east-2.compute.internal"


class FakeEc2:
    def __init__(self, instances: dict[str, dict]) -> None:
        self.instances = instances
        self.error: Exception | None = None

    def describe_instances(self, InstanceIds):
        if self.error:
            raise self.error
        instance = self.instances.get(InstanceIds[0])
        if instance is None:
            raise ClientError({"Error": {"Code": "InvalidInstanceID.NotFound"}}, "DescribeInstances")
        return {"Reservations": [{"Instances": [instance]}]}


class FakeIam:
    def __init__(self, profiles: dict[str, str]) -> None:
        self.profiles = profiles
        self.calls = 0

    def get_instance_profile(self, InstanceProfileName):
        self.calls += 1
        return {"InstanceProfile": {"Roles": [{"Arn": self.profiles[InstanceProfileName]}]}}


class FakeCore:
    def __init__(self, nodes: list) -> None:
        self.nodes = nodes
        self.patches: list[tuple[str, dict]] = []

    def list_node(self):
        return SimpleNamespace(items=self.nodes)

    def patch_node(self, name, body):
        self.patches.append((name, body))


def _node(name=NODE, instance="i-0abc12345678def00", labels=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels or {}),
        spec=SimpleNamespace(provider_id=f"aws:///us-east-2a/{instance}" if instance else None),
    )


def _instance(profile="eks-pool", dns=NODE, state="running"):
    return {
        "State": {"Name": state},
        "PrivateDnsName": dns,
        "IamInstanceProfile": {"Arn": f"arn:aws:iam::111122223333:instance-profile/{profile}"},
    }


@pytest.fixture
def ec2():
    return FakeEc2(
        {
            "i-0abc12345678def00": _instance(),
            "i-0abc12345678def01": _instance(profile="eks-platform", dns=PLATFORM_NODE),
            "i-0abc12345678def02": _instance(dns="ip-10-0-9-9.us-east-2.compute.internal"),
            "i-0abc12345678def03": _instance(state="stopped"),
        }
    )


@pytest.fixture
def iam():
    return FakeIam({"eks-pool": POOL_ROLE, "eks-platform": PLATFORM_ROLE})


def test_labels_only_nodes_backed_by_the_pool_role(ec2, iam):
    pool = _node()
    platform = _node(name=PLATFORM_NODE, instance="i-0abc12345678def01")
    core = FakeCore([pool, platform])

    reconcile_all(core, InstanceRoleResolver(ec2, iam), POOL_ROLE)

    assert core.patches == [(NODE, {"metadata": {"labels": {POOL_LABEL: POOL_VALUE}}})]
    # An already-labeled pool node is left alone, and the profile lookup is cached.
    core = FakeCore([_node(labels={POOL_LABEL: POOL_VALUE})])
    resolver = InstanceRoleResolver(ec2, iam)
    reconcile_all(core, resolver, POOL_ROLE)
    reconcile_all(core, resolver, POOL_ROLE)
    assert core.patches == []
    assert iam.calls == 3


def test_removes_the_label_from_any_node_that_fails_verification(ec2, iam):
    labeled = {POOL_LABEL: POOL_VALUE}
    nodes = [
        _node(name=PLATFORM_NODE, instance="i-0abc12345678def01", labels=dict(labeled)),  # platform role
        _node(name=NODE, instance="i-0abc12345678def02", labels=dict(labeled)),  # DNS name mismatch
        _node(name=NODE, instance="i-0abc12345678def03", labels=dict(labeled)),  # stopped
        _node(name="ip-b", instance="i-0abc12345678def99", labels=dict(labeled)),  # missing instance
        _node(name="ip-c", instance=None, labels=dict(labeled)),  # no provider ID
    ]
    core = FakeCore(nodes)

    reconcile_all(core, InstanceRoleResolver(ec2, iam), POOL_ROLE)

    assert [name for name, _ in core.patches] == [node.metadata.name for node in nodes]
    assert all(body == {"metadata": {"labels": {POOL_LABEL: None}}} for _, body in core.patches)


def test_provider_errors_leave_nodes_unchanged(ec2, iam):
    ec2.error = EndpointConnectionError(endpoint_url="https://ec2.us-east-2.amazonaws.com")
    core = FakeCore([_node(), _node(name="ip-x", labels={POOL_LABEL: POOL_VALUE})])

    reconcile_all(core, InstanceRoleResolver(ec2, iam), POOL_ROLE)

    assert core.patches == []
