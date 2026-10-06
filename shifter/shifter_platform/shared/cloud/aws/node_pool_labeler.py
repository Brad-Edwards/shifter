"""Trusted labeler for the EKS runtime-plugin node pool (#2526).

Runtime-plugin worker Jobs select ``node-restriction.kubernetes.io/shifter-pool=
runtime-plugin``. NodeRestriction forbids a kubelet from setting labels under
that prefix, which is what makes the label a trustworthy pool identity: a node
cannot place itself into (or out of) the sandbox pool. GKE applies the label
through its control plane; EKS managed node groups apply labels through the
kubelet, so on EKS this controller applies it instead.

A node qualifies only when the EC2 instance behind it is running, its private
DNS name equals the node name the EKS authenticator assigned, and its instance
profile's role is the runtime-plugin pool's dedicated node role. None of these
can be changed from the node. A node that positively fails the check loses the
label; a provider error leaves the node unchanged.

Usage: ``python -m shared.cloud.aws.node_pool_labeler`` with
``RUNTIME_PLUGIN_NODE_ROLE_ARN`` and ``AWS_REGION`` in the environment.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POOL_LABEL = "node-restriction.kubernetes.io/shifter-pool"
POOL_VALUE = "runtime-plugin"
HEARTBEAT = Path("/tmp/node-pool-labeler-heartbeat")  # noqa: S108 # nosec B108 # NOSONAR(S5443)
_PROVIDER_ID = re.compile(r"^aws:///[a-z0-9-]+/(i-[0-9a-f]{8,17})$")
_POLL_SECONDS = 15


class InstanceRoleResolver:
    """Resolve the IAM roles behind a node's EC2 instance."""

    def __init__(self, ec2: Any, iam: Any) -> None:
        self._ec2 = ec2
        self._iam = iam
        self._profile_roles: dict[str, frozenset[str]] = {}

    def roles(self, instance_id: str, node_name: str) -> frozenset[str]:
        """Roles of the running instance whose private DNS name is ``node_name``.

        Returns an empty set when the instance does not exist, is not running,
        does not match the node name, or has no instance profile. Provider
        errors other than a missing instance propagate.
        """
        from botocore.exceptions import ClientError

        try:
            response = self._ec2.describe_instances(InstanceIds=[instance_id])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "InvalidInstanceID.NotFound":
                return frozenset()
            raise
        instances = [instance for reservation in response["Reservations"] for instance in reservation["Instances"]]
        if len(instances) != 1:
            return frozenset()
        instance = instances[0]
        if instance.get("State", {}).get("Name") != "running" or instance.get("PrivateDnsName") != node_name:
            return frozenset()
        profile_arn = instance.get("IamInstanceProfile", {}).get("Arn", "")
        if not profile_arn:
            return frozenset()
        if profile_arn not in self._profile_roles:
            profile = self._iam.get_instance_profile(InstanceProfileName=profile_arn.rsplit("/", 1)[-1])
            self._profile_roles[profile_arn] = frozenset(role["Arn"] for role in profile["InstanceProfile"]["Roles"])
        return self._profile_roles[profile_arn]


def node_qualifies(node: Any, resolver: InstanceRoleResolver, pool_role_arn: str) -> bool | None:
    """Whether ``node`` belongs to the pool; ``None`` when the provider could not answer."""
    from botocore.exceptions import BotoCoreError, ClientError

    match = _PROVIDER_ID.fullmatch(node.spec.provider_id or "")
    if match is None:
        return False
    try:
        return pool_role_arn in resolver.roles(match.group(1), node.metadata.name)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("node pool check deferred node=%s error=%s", node.metadata.name, type(exc).__name__)
        return None


def reconcile_node(core: Any, node: Any, resolver: InstanceRoleResolver, pool_role_arn: str) -> None:
    """Apply or remove the pool label so it matches the node's verified identity."""
    qualifies = node_qualifies(node, resolver, pool_role_arn)
    if qualifies is None:
        return
    current = (node.metadata.labels or {}).get(POOL_LABEL)
    if qualifies and current != POOL_VALUE:
        core.patch_node(node.metadata.name, {"metadata": {"labels": {POOL_LABEL: POOL_VALUE}}})
        logger.info("node pool label applied node=%s", node.metadata.name)
    elif not qualifies and current is not None:
        core.patch_node(node.metadata.name, {"metadata": {"labels": {POOL_LABEL: None}}})
        logger.warning("node pool label removed from unverified node=%s", node.metadata.name)


def reconcile_all(core: Any, resolver: InstanceRoleResolver, pool_role_arn: str) -> None:
    """One pass over every node."""
    for node in core.list_node().items:
        reconcile_node(core, node, resolver, pool_role_arn)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pool_role_arn = os.environ.get("RUNTIME_PLUGIN_NODE_ROLE_ARN", "")
    region = os.environ.get("AWS_REGION", "")
    if not pool_role_arn.startswith("arn:aws:iam::") or not region:
        sys.stderr.write("RUNTIME_PLUGIN_NODE_ROLE_ARN and AWS_REGION are required\n")
        return 2
    import boto3

    from shared.cloud.kubernetes._client import load_kubernetes_api

    _batch, core, _client, api_exception = load_kubernetes_api()
    resolver = InstanceRoleResolver(boto3.client("ec2", region_name=region), boto3.client("iam"))
    while True:
        try:
            reconcile_all(core, resolver, pool_role_arn)
            HEARTBEAT.touch()
        except api_exception as exc:
            logger.warning("node pool reconcile deferred status=%s", getattr(exc, "status", "unknown"))
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
