"""Terraform provision / destroy + Terraform variable builders for Shifter ranges.

Extracted from ``main.py`` (Sonar S104). Owns the run_range_terraform
dispatch path, the per-operation provision and destroy pipelines, the
NGFW recovery path that runs before provisioning, the NGFW-on-range
attachment helpers that run after the Terraform apply, and the
``_build_range_terraform_variables`` family that maps the range spec
into the inputs the Terraform module expects.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.range_cells import RangeCellContractError, validate_scenario_artifact
from shared.range_instantiation_policy import PREREQUISITE_DENIAL_CODE, normalize_gcp_range_backend

import range_terraform_runner
from cloud.exceptions import CloudError
from config import is_gce_range_cell_backend, load_range_network_config, resolve_cloud_provider
from events import publish_destroyed, publish_failed, publish_ready, publish_status_update
from instance_orchestrator import run_instance_setup
from provisioner_db import (
    _update_range_config,
    get_range_data_by_request_id,
    mark_range_instances_destroyed,
    resolve_legacy_range_backend,
    write_provisioned_state,
)
from state_helpers import _validate_provisioned_outputs
from terraform_ngfw_range import (
    _configure_ngfw_for_range,
    _ensure_ngfw_ready_for_provisioning,
    _maybe_pause_user_ngfw,
    _remove_ngfw_attachments_for_destroy,
    _validate_ngfw_range_attachment,
)
from terraform_vars import build_range_variables

logger = logging.getLogger(__name__)


def _prerequisite_error(message: str) -> CloudError:
    """Build a fail-closed ADR-039 ``prerequisite`` CloudError with an authored message."""
    error = CloudError(message)
    error.code = PREREQUISITE_DENIAL_CODE
    return error


def _resolve_legacy_gcp_backend(range_data: dict[str, Any]) -> str:
    """Resolve a GCP range with no persisted binding from durable ownership evidence (#1666).

    A pre-#1666 (legacy) range carries no ownership binding. On destroy/reconcile
    we must never guess the backend from the mutable env selector -- after a
    ``gdc -> gce`` flip that would strand the range. Resolve only from durable,
    ownership-proven evidence (provider/asset discriminants persisted on the
    range's ``engine_instance.state`` rows, or an explicit operator backfill of
    the binding). An ambiguous or evidence-free row fails closed with a
    ``prerequisite`` diagnostic and retains its cleanup state for explicit repair.
    """
    request_id = range_data["request_id"]
    resolved = resolve_legacy_range_backend(request_id)
    if resolved is not None:
        logger.info(
            "Resolved legacy GCP range backend from ownership evidence request_id=%s backend=%s",
            request_id,
            resolved,
        )
        return resolved
    raise _prerequisite_error(
        "This GCP range predates backend ownership binding and its backend could not be proven from "
        "durable ownership evidence. Back-fill its range_backend with the operator command "
        "(manage.py backfill_range_backend_binding) while the historical selector is known, then retry. "
        "The range's cleanup state is retained; no resources were touched."
    )


def _resolve_operation_backend(range_data: dict[str, Any], operation: str) -> str | None:
    """Resolve the per-operation GCP range backend from persisted ownership (#1666).

    Returns the normalized write-once binding when present; ``None`` for non-GCP
    (AWS) ranges, where gce/gdc routing does not apply. For a GCP range with no
    persisted binding, a destroy/reconcile resolves from durable ownership
    evidence (or fails closed); provision (and its immediate compensation) fall
    back to the env selector, since a fresh range has no resources to disambiguate
    and the selector still equals what was admitted in that window.
    """
    persisted = range_data.get("range_backend")
    if persisted:
        return normalize_gcp_range_backend(persisted)
    if resolve_cloud_provider() != "gcp":
        return None
    if operation != "destroy":
        return None
    return _resolve_legacy_gcp_backend(range_data)


def _release_subnet_allocations_best_effort(request_id: str) -> None:
    """Release subnet allocations on provision failure; never raise."""
    try:
        from components.network import release_subnet_allocations

        release_subnet_allocations(request_id)
    except Exception as e:
        logger.warning("Failed to release subnet allocations: %s", e)


def _build_operation_variables(
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    scenario_artifact: dict[str, Any] | None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Build backend variables while preserving legacy call behavior.

    ``backend`` (the #1666 per-operation binding) selects the variable shape from
    persisted ownership for destroy/compensation; ``None`` keeps the env-selector
    behavior for provision and non-GCP callers.
    """
    if scenario_artifact is None:
        return build_range_variables(request_id, range_id, user_id, range_spec, backend=backend)
    return build_range_variables(
        request_id,
        range_id,
        user_id,
        range_spec,
        scenario_artifact=scenario_artifact,
        backend=backend,
    )


def _attempt_terraform_auto_cleanup(
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    *,
    scenario_artifact: dict[str, Any] | None = None,
    backend: str | None = None,
) -> None:
    """Best-effort `terraform destroy` after a failed provision.

    ``backend`` is the binding captured once at operation start (#1666); the
    compensation destroy routes from it, not a re-read of the env selector.
    """
    logger.error(
        "Provision failed for range_id=%s request_id=%s - attempting Terraform cleanup...",
        range_id,
        request_id,
    )
    try:
        cleanup_variables = _build_operation_variables(
            request_id,
            range_id,
            user_id,
            range_spec,
            scenario_artifact,
            backend,
        )
        range_terraform_runner.destroy_range(request_id, variables=cleanup_variables, backend=backend)
        range_terraform_runner.cleanup_range_state(request_id, backend)
        logger.info("Auto-cleanup succeeded for range_id=%s", range_id)
    except Exception:
        logger.exception(
            "Auto-cleanup FAILED for range_id=%s request_id=%s. "
            "Orphaned cloud resources may exist and require manual cleanup.",
            range_id,
            request_id,
        )
    _release_subnet_allocations_best_effort(request_id)


def _dispatch_terraform_operation(
    operation: str,
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    *,
    scenario_artifact: dict[str, Any] | None = None,
    backend: str | None = None,
) -> None:
    """Run the requested Terraform operation; raise ValueError for unknown ops.

    ``backend`` (the #1666 per-operation ownership binding) routes destroy from
    persisted ownership; provision keeps its #1348 env-gated routing unchanged.
    """
    if operation == "up":
        _run_terraform_provision(request_id, range_id, user_id, range_spec, scenario_artifact=scenario_artifact)
    elif operation == "destroy":
        _run_terraform_destroy(
            request_id,
            range_id,
            user_id,
            range_spec,
            scenario_artifact=scenario_artifact,
            backend=backend,
        )
    else:
        raise ValueError(f"Unknown operation: {operation}")


def _safe_failure_message(exc: Exception) -> str:
    """Return a bounded authored event message for a lifecycle failure."""
    if isinstance(exc, RangeCellContractError):
        return "Range-cell contract validation failed"
    return str(exc)[:1000]


def run_range_terraform(operation: str, request_id: str) -> None:
    """Run Range Terraform operation (provision or destroy)."""
    logger.info("run_range_terraform: starting operation=%s request_id=%s", operation, request_id)

    range_data = get_range_data_by_request_id(request_id)
    range_id = range_data["range_id"]
    user_id = range_data["user_id"]
    range_spec = range_data.get("spec", {})
    # Resolve the per-operation backend binding once, at operation start, from the
    # persisted Range ownership (#1666). Reused for dispatch and, on a provision
    # failure, for the compensation destroy -- never re-resolved from the env
    # selector after a failure.
    operation_backend = _resolve_operation_backend(range_data, operation)

    scenario_artifact = None
    operation_dispatched = False
    try:
        # Verify the producer-minted artifact before any NGFW or provider
        # operation. Other backends retain their existing legacy payload path.
        if is_gce_range_cell_backend():
            scenario_artifact = validate_scenario_artifact(range_data.get("spec_envelope"))

        if range_spec.get("ngfw", False):
            _ensure_ngfw_ready_for_provisioning(range_id, user_id)

        operation_dispatched = True
        _dispatch_terraform_operation(
            operation,
            request_id,
            range_id,
            user_id,
            range_spec,
            scenario_artifact=scenario_artifact,
            backend=operation_backend,
        )
    except Exception as e:
        error_msg = _safe_failure_message(e)
        logger.exception("Range Terraform operation failed: %s", error_msg)
        if operation == "up" and operation_dispatched:
            _attempt_terraform_auto_cleanup(
                request_id,
                range_id,
                user_id,
                range_spec,
                scenario_artifact=scenario_artifact,
                backend=operation_backend,
            )
        publish_failed(
            request_id=request_id,
            range_id=range_id,
            user_id=user_id,
            error_message=error_msg,
        )
        raise


def _run_terraform_provision(
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    *,
    scenario_artifact: dict[str, Any] | None = None,
) -> None:
    """Run Terraform apply for range, then run instance setup."""
    publish_status_update(
        request_id=request_id,
        range_id=range_id,
        user_id=user_id,
        new_status="provisioning",
    )

    logger.info("Running terraform apply for range...")

    spec_subnets = _allocate_range_subnet_cidrs(
        request_id,
        range_id,
        range_spec,
        persist_to_scenario=not is_gce_range_cell_backend(),
    )

    # Build backend-appropriate range variables from the range spec (now with
    # CIDRs). GCE range cells receive a closed request around the persisted
    # scenario artifact; AWS receives Terraform variables.
    provision_variables = _build_operation_variables(
        request_id,
        range_id,
        user_id,
        range_spec,
        scenario_artifact,
    )

    # Run the provider-routed apply
    output_data = range_terraform_runner.apply_range(request_id, provision_variables)

    subnets_output = output_data.get("subnets", {})
    instances_output = output_data.get("instances", [])
    # Non-secret per-range Polaris Bedrock agent role ARN (#1377); empty
    # string when polaris_agent_enabled was false or the backend has no
    # such output (e.g. GCP/GDC ranges).
    polaris_agent_role_arn = output_data.get("polaris_agent_role_arn") or ""
    # Log structure only. Terraform outputs can carry sensitive values
    # (generated passwords, SSH keys, tokens) and must never be dumped raw.
    logger.info(
        "Terraform apply produced %d subnet(s) and %d instance(s)",
        len(subnets_output),
        len(instances_output),
    )

    expected_subnet_names = {str(subnet_name) for subnet in spec_subnets if (subnet_name := subnet.get("name"))}
    _validate_provisioned_outputs(
        subnets=subnets_output,
        instances=instances_output,
        expected_subnet_names=expected_subnet_names,
    )

    _validate_ngfw_range_attachment(range_spec, user_id)
    _configure_ngfw_for_range(
        request_id=request_id,
        range_id=range_id,
        user_id=user_id,
        range_spec=range_spec,
        spec_subnets=spec_subnets,
        subnets_output=subnets_output,
    )

    # Run instance setup (DC first, then others in parallel)
    logger.info("Running instance setup...")
    run_instance_setup(
        instances_output=instances_output,
        range_spec=range_spec,
        range_id=range_id,
        polaris_agent_role_arn=polaris_agent_role_arn,
    )

    # Write provisioned state to DB
    range_data = get_range_data_by_request_id(request_id)
    write_provisioned_state(
        range_id=range_id,
        subnets=subnets_output,
        instances=instances_output,
        ngfw_instance_id=range_data.get("ngfw_instance_id"),
    )

    publish_ready(request_id=request_id, range_id=range_id, user_id=user_id)


def _allocate_range_subnet_cidrs(
    request_id: str,
    range_id: int,
    range_spec: dict[str, Any],
    *,
    persist_to_scenario: bool = True,
) -> list[dict[str, Any]]:
    """Allocate subnet CIDRs, optionally retaining legacy scenario persistence."""
    spec_subnets = range_spec.get("subnets", [])
    if not spec_subnets:
        return spec_subnets

    from components.network import allocate_subnets

    # Fallback CIDR used only when the network config has no explicit network_cidr;
    # matches the dev environment's default range VPC. Production callers always
    # populate range_network.network_cidr from environment terraform.
    _DEFAULT_RANGE_VPC_CIDR = "10.1.0.0/16"  # NOSONAR — documented fallback CIDR, prod overrides via terraform
    range_network = load_range_network_config()
    vpc_id = range_network.network_id
    vpc_cidr = range_network.network_cidr or _DEFAULT_RANGE_VPC_CIDR
    cidr_prefix = ".".join(vpc_cidr.split("/")[0].split(".")[:2])
    subnet_count = len(spec_subnets)
    logger.info("Allocating %d subnet CIDRs in VPC %s", subnet_count, vpc_id)
    allocated_cidrs = allocate_subnets(
        vpc_id,
        cidr_prefix,
        subnet_count,
        subnet_size=28,
        range_id=range_id,
        request_id=request_id,
    )
    logger.info("Allocated CIDRs: %s", allocated_cidrs)
    for i, subnet in enumerate(spec_subnets):
        subnet["cidr"] = allocated_cidrs[i]
    if persist_to_scenario:
        _update_range_config(range_id, range_spec)
    return spec_subnets


def _recover_missing_subnet_cidrs(range_id: int, range_spec: dict[str, Any]) -> None:
    """If range_spec lost its subnet CIDRs, repopulate from the allocation table."""
    spec_subnets = range_spec.get("subnets", [])
    if not spec_subnets or spec_subnets[0].get("cidr"):
        return
    logger.warning("range_config missing CIDRs for range %d, recovering from allocation table", range_id)
    from components.network import get_allocated_cidrs

    allocated = get_allocated_cidrs(range_id)
    for i, subnet in enumerate(spec_subnets):
        if i < len(allocated):
            subnet["cidr"] = allocated[i]


def _post_destroy_cleanup(request_id: str, range_id: int) -> None:
    """Mark range destroyed, release subnet allocations. Best-effort."""
    try:
        mark_range_instances_destroyed(range_id)
    except Exception:
        logger.exception("Failed to mark range %d as destroyed", range_id)

    try:
        from components.network import release_subnet_allocations

        release_subnet_allocations(request_id)
    except Exception as e:
        logger.warning("Failed to release subnet allocations: %s", e)


def _ensure_range_is_active(request_id: str, range_id: int) -> bool:
    """Return True if the range exists and is not already destroyed."""
    try:
        range_data = get_range_data_by_request_id(request_id)
    except ValueError as e:
        logger.warning("Range not found for request %s, skipping destroy: %s", request_id, e)
        return False
    if range_data.get("status") == "destroyed":
        logger.info("Range %d already destroyed, skipping", range_id)
        return False
    return True


def _run_terraform_destroy(
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    *,
    scenario_artifact: dict[str, Any] | None = None,
    backend: str | None = None,
) -> None:
    """Run Terraform destroy for range.

    ``backend`` is the #1666 per-operation ownership binding: teardown routes from
    the backend the range was provisioned on, not the current env selector, so a
    ``gdc -> gce`` deploy-selector flip cannot strand an existing GDC range.
    """
    if not _ensure_range_is_active(request_id, range_id):
        return

    _remove_ngfw_attachments_for_destroy(user_id, range_id, range_spec)
    _recover_missing_subnet_cidrs(range_id, range_spec)

    logger.info("Running terraform destroy for range...")
    terraform_succeeded = False
    try:
        destroy_variables = _build_operation_variables(
            request_id,
            range_id,
            user_id,
            range_spec,
            scenario_artifact,
            backend,
        )
        range_terraform_runner.destroy_range(request_id, variables=destroy_variables, backend=backend)
        terraform_succeeded = True
        logger.info("Cleaning up Terraform state...")
        range_terraform_runner.cleanup_range_state(request_id, backend)
    finally:
        if terraform_succeeded:
            _post_destroy_cleanup(request_id, range_id)
        _maybe_pause_user_ngfw(user_id, range_id)

    publish_destroyed(request_id=request_id, range_id=range_id, user_id=user_id)
