"""Polaris-specific post-bootstrap helper for the Shifter Engine provisioner.

Extracted from ``instance_setup.py`` (Sonar S104). Owns the post-Linux-
bootstrap rewrite of the polaris-vm AMI's docker compose stack so each
range gets its own DC IP and per-instance kali pubkey instead of the
bake-time defaults.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from executors.factory import build_guest_execution_context
from orchestrators.setup_orchestrator import SetupError, SetupOrchestrator
from plans.polaris_range_bootstrap import PolarisRangeBootstrapPlan

logger = logging.getLogger(__name__)

# The polaris-vm host is already set up (LinuxBootstrapPlan) before this runs,
# but building a fresh execution context reconnects the transport, so give the
# in-range GDC pod-SSH path (cloud-init + host-key install) a generous budget.
_POLARIS_READY_TIMEOUT_SECONDS = 600


def _run_polaris_range_bootstrap(
    instance_id: str,
    dc_ip: str,
    public_key: str,
    instance_data: dict[str, Any],
    os_type: str = "ubuntu",
    role: str = "attacker",
) -> None:
    """Run PolarisRangeBootstrapPlan against a polaris VM instance.

    Transport-agnostic: the executor is selected per platform via
    ``build_guest_execution_context`` (SSM on AWS, in-range pod SSH on GDC), so
    the same override-rewrite + container-recreate plan runs on either plane.
    """
    if not dc_ip:
        raise SetupError(
            f"polaris range bootstrap for {instance_id}: dc_ip is empty "
            "(scenario must include a role=dc instance so the DC's "
            "private IP can be discovered)"
        )
    if not public_key:
        raise SetupError(
            f"polaris range bootstrap for {instance_id}: public_key is empty "
            "(per-instance ssh key from tls_private_key.instance was not propagated)"
        )

    logger.info(
        "Running polaris range bootstrap on %s (dc_ip=%s, key length=%d)",
        instance_id,
        dc_ip,
        len(public_key),
    )

    # AWS-only: bump the IMDSv2 PutResponseHopLimit to 2 so the a14-kali
    # container (one hop past the host netns through the docker bridge) can
    # reach IMDS at 169.254.169.254 for the EC2 role creds. GDC has no IMDS,
    # so this is skipped there. Idempotent on AWS.
    if os.environ.get("CLOUD_PROVIDER", "aws").lower() == "aws":
        try:
            import boto3 as _boto3

            _ec2 = _boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "us-east-2"))
            _ec2.modify_instance_metadata_options(
                InstanceId=instance_id,
                HttpPutResponseHopLimit=2,
                HttpTokens="required",
                HttpEndpoint="enabled",
            )
            logger.info("Set IMDSv2 hop limit=2 on %s for kali container reachability", instance_id)
        except Exception as e:
            # Warn rather than fail provisioning — claude inside kali will surface
            # the loss of creds at runtime if this slip propagates that far.
            logger.warning("failed to set IMDS hop limit on %s: %s", instance_id, e)

    execution = build_guest_execution_context(instance_data, os_type=os_type, role=role)
    orchestrator = SetupOrchestrator(executor=execution.executor)
    plan = PolarisRangeBootstrapPlan()

    class _PolarisCtx:
        """Local context shim for PolarisRangeBootstrapPlan template variables."""

        def __init__(self) -> None:
            self.dc_ip = dc_ip
            self.public_key = public_key

    context = plan.get_context(_PolarisCtx())
    try:
        logger.info("Waiting for %s connectivity on %s...", execution.transport_name, execution.target)
        execution.wait_for_ready(timeout_seconds=_POLARIS_READY_TIMEOUT_SECONDS)
        result = orchestrator.orchestrate(
            execution.target,
            plan,
            context,
            document_name=execution.document_name,
        )
    finally:
        execution.close()
    if not result.success:
        raise SetupError(f"polaris range bootstrap failed on {instance_id}: {result.error}")
    logger.info("polaris range bootstrap complete for %s", instance_id)
