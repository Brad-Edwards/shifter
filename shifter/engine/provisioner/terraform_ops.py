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
from dataclasses import dataclass
from typing import Any

from shared.range_cells import RangeCellContractError, validate_scenario_artifact
from shared.range_instantiation_policy import (
    POLICY_DENIAL_CODE,
    PREREQUISITE_DENIAL_CODE,
    InstantiationPurpose,
    evaluate_gcp_backend_admission,
    normalize_gcp_range_backend,
)
from shared.remote_access import parse_openvpn_capability, validate_openvpn_capability_window

import range_terraform_runner
from cloud.exceptions import CloudError
from config import is_gce_range_cell_backend, resolve_cloud_provider
from events import (
    STATUS_DESTROYED,
    STATUS_PROVISIONING,
    publish_destroyed,
    publish_failed,
    publish_ready,
    publish_status_update,
)
from gcp_range_cell_scenario import validate_legacy_gce_composition
from instance_orchestrator import run_instance_setup
from provisioner_db import (
    get_range_data_by_request_id,
    write_provisioned_state,
)
from provisioner_db_appends import OperationRef
from range_backend_evidence import resolve_legacy_range_backend
from range_subnet_allocation import (
    _allocate_range_subnet_cidrs,
    _post_destroy_cleanup,
    _recover_missing_subnet_cidrs,
    _release_subnet_allocations_best_effort,
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
from vpn_access import (
    cleanup_openvpn_access,
    finalize_openvpn_access,
    prepare_openvpn_access,
    verify_openvpn_gateway,
)
from vpn_secrets import get_vpn_secret_ops, openvpn_access_enabled

logger = logging.getLogger(__name__)


def _cleanup_openvpn_if_enabled(range_id: int, request_id: str, *, delete_identity: bool = True) -> None:
    """Delete a generation only when this installation could have created it."""
    if openvpn_access_enabled():
        cleanup_openvpn_access(
            range_id,
            request_id,
            get_vpn_secret_ops(),
            delete_identity=delete_identity,
        )


def _prerequisite_error(message: str) -> CloudError:
    """Build a fail-closed ADR-039 ``prerequisite`` CloudError with an authored message."""
    error = CloudError(message)
    error.code = PREREQUISITE_DENIAL_CODE
    return error


def _coded_error(message: str, code: str) -> CloudError:
    """Build a CloudError carrying an authored stable failure classification."""
    error = CloudError(message)
    error.code = code
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
    evidence (or fails closed). A normal provision must already carry the
    admission-time binding and never falls back to the deploy-wide selector.
    """
    persisted = range_data.get("range_backend")
    if persisted:
        return normalize_gcp_range_backend(persisted)
    # A normal GCP provision is admitted and bound before dispatch. Re-reading
    # the deploy-wide selector here would allow an in-flight selector flip to
    # change ownership, so a missing binding fails closed.
    if resolve_cloud_provider() != "gcp":
        return None
    if operation != "destroy":
        raise _prerequisite_error(
            "This GCP range has no persisted backend ownership binding; retry the launch after admission"
        )
    return _resolve_legacy_gcp_backend(range_data)


def _resolve_operation_purpose(
    range_data: dict[str, Any],
    backend: str | None,
    operation: str,
) -> InstantiationPurpose | None:
    """Validate the persisted launch purpose for a bound normal GCP operation."""
    if backend is None:
        return None
    raw_purpose = range_data.get("instantiation_purpose")
    if raw_purpose is None and operation == "destroy":
        # Legacy cleanup remains possible; backend ownership is sufficient to
        # select the deterministic teardown adapter.
        return None
    try:
        purpose = InstantiationPurpose(raw_purpose)
    except (TypeError, ValueError):
        raise _prerequisite_error("This GCP range has no valid persisted instantiation purpose") from None
    if operation != "destroy":
        if purpose is not InstantiationPurpose.LIVE_FIRE:
            raise _coded_error(
                "Normal GCP range provisioning requires the live_fire instantiation purpose",
                POLICY_DENIAL_CODE,
            )
        admission = evaluate_gcp_backend_admission(backend, None, purpose)
        if not admission.admitted:
            raise _coded_error(admission.reason, admission.code)
    return purpose


@dataclass(frozen=True)
class RangeOperation:
    """Inputs of one range Terraform operation, bound once at dispatch.

    ``backend`` is the #1666 per-operation ownership binding captured at
    operation start; on a provision failure the compensation destroy routes
    from it, never a re-read of the env selector.

    ``operation_id`` is the ADR-043 canonical operation generation (#1834);
    ``None`` on local-dev runs / commands not yet carrying it.
    """

    request_id: str
    range_id: int
    user_id: int
    range_spec: dict[str, Any]
    scenario_artifact: dict[str, Any] | None = None
    backend: str | None = None
    purpose: InstantiationPurpose | None = None
    remote_access_capability: dict[str, object] | None = None
    operation_id: str | None = None


def _build_operation_variables(
    request_id: str,
    range_id: int,
    user_id: int,
    range_spec: dict[str, Any],
    scenario_artifact: dict[str, Any] | None,
    backend: str | None = None,
    remote_access_capability: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Build backend variables while preserving legacy call behavior.

    ``backend`` (the #1666 per-operation binding) selects the variable shape from
    persisted ownership for destroy/compensation; ``None`` keeps the env-selector
    behavior for provision and non-GCP callers.
    """
    kwargs: dict[str, Any] = {"backend": backend}
    if remote_access_capability is not None:
        kwargs["remote_access_capability"] = remote_access_capability
    if scenario_artifact is not None:
        kwargs["scenario_artifact"] = scenario_artifact
    return build_range_variables(request_id, range_id, user_id, range_spec, **kwargs)


def _attempt_terraform_auto_cleanup(operation: RangeOperation) -> None:
    """Best-effort `terraform destroy` after a failed provision."""
    logger.error(
        "Provision failed for range_id=%s request_id=%s - attempting Terraform cleanup...",
        operation.range_id,
        operation.request_id,
    )
    try:
        cleanup_variables = _build_operation_variables(
            operation.request_id,
            operation.range_id,
            operation.user_id,
            operation.range_spec,
            operation.scenario_artifact,
            operation.backend,
            operation.remote_access_capability,
        )
        range_terraform_runner.destroy_range(
            operation.request_id, variables=cleanup_variables, backend=operation.backend
        )
        range_terraform_runner.cleanup_range_state(operation.request_id, operation.backend)
        logger.info("Auto-cleanup succeeded for range_id=%s", operation.range_id)
    except Exception:
        logger.exception(
            "Auto-cleanup FAILED for range_id=%s request_id=%s. "
            "Orphaned cloud resources may exist and require manual cleanup.",
            operation.range_id,
            operation.request_id,
        )
    finally:
        if operation.remote_access_capability is not None:
            try:
                # Preserve the deterministic GCE principal across retry. A
                # terminal range destroy deletes it; compensation revokes all
                # secrets but avoids GCP's 30-day service-account tombstone.
                _cleanup_openvpn_if_enabled(operation.range_id, operation.request_id, delete_identity=False)
            except Exception:
                logger.exception("Failed to revoke OpenVPN generation during provision compensation")
    _release_subnet_allocations_best_effort(operation.request_id)


def _dispatch_terraform_operation(kind: str, operation: RangeOperation) -> None:
    """Run the requested Terraform operation; raise ValueError for unknown ops.

    ``operation.backend`` (the #1666 per-operation ownership binding) routes
    destroy from persisted ownership; provision keeps its #1348 env-gated
    routing unchanged.
    """
    if kind == "up":
        _run_terraform_provision(operation)
    elif kind == "destroy":
        _run_terraform_destroy(operation)
    else:
        raise ValueError(f"Unknown operation: {kind}")


def _safe_failure_message(exc: Exception) -> str:
    """Return a bounded authored event message for a lifecycle failure."""
    if isinstance(exc, RangeCellContractError):
        return "Range-cell contract validation failed"
    return str(exc)[:1000]


def _resolve_remote_access_capability(
    range_data: dict[str, Any],
    operation: str,
) -> dict[str, object] | None:
    """Validate persisted remote-access authority before any operation mutation."""
    raw_capability = range_data.get("remote_access_capability")
    if raw_capability is None:
        return None
    capability = parse_openvpn_capability(raw_capability)
    if operation == "up":
        validate_openvpn_capability_window(capability)
        if not openvpn_access_enabled():
            raise _prerequisite_error(
                "This range requests OpenVPN access, but the selected provider adapter is not configured to realize it"
            )
    return capability.as_dict()


def run_range_terraform(operation: str, request_id: str, *, operation_id: str | None = None) -> None:
    """Run Range Terraform operation (provision or destroy).

    ``operation_id`` is the ADR-043 canonical operation generation (#1834),
    threaded onto the argv only on the remote/drainer dispatch path; ``None``
    on local-dev runs.
    """
    logger.info("run_range_terraform: starting operation=%s request_id=%s", operation, request_id)

    range_data = get_range_data_by_request_id(request_id)
    range_id = range_data["range_id"]
    user_id = range_data["user_id"]
    range_spec = range_data.get("spec", {})
    # Resolve the per-operation backend binding once, at operation start, from the
    # persisted Range ownership (#1666). Reused for dispatch and, on a provision
    # failure, for the compensation destroy -- never re-resolved from the env
    # selector after a failure.
    range_operation: RangeOperation | None = None
    try:
        operation_backend = _resolve_operation_backend(range_data, operation)
        operation_purpose = _resolve_operation_purpose(range_data, operation_backend, operation)
        remote_access_capability = _resolve_remote_access_capability(range_data, operation)
        # Verify the producer-minted artifact before any NGFW or provider
        # operation. Other backends retain their existing legacy payload path.
        uses_gce = operation_backend == "gce" if operation_backend is not None else is_gce_range_cell_backend()
        scenario_artifact = validate_scenario_artifact(range_data.get("spec_envelope")) if uses_gce else None
        if operation == "up" and scenario_artifact is not None:
            validate_legacy_gce_composition(scenario_artifact, backend=operation_backend)

        if range_spec.get("ngfw", False):
            _ensure_ngfw_ready_for_provisioning(range_id, user_id)

        range_operation = RangeOperation(
            request_id=request_id,
            range_id=range_id,
            user_id=user_id,
            range_spec=range_spec,
            scenario_artifact=scenario_artifact,
            backend=operation_backend,
            purpose=operation_purpose,
            remote_access_capability=remote_access_capability,
            operation_id=operation_id,
        )
        _dispatch_terraform_operation(operation, range_operation)
    except Exception as e:
        error_msg = _safe_failure_message(e)
        logger.exception("Range Terraform operation failed: %s", error_msg)
        if operation == "up" and range_operation is not None:
            _attempt_terraform_auto_cleanup(range_operation)
        publish_failed(
            request_id=request_id,
            range_id=range_id,
            user_id=user_id,
            error_message=error_msg,
        )
        raise


def _run_terraform_provision(operation: RangeOperation) -> None:
    """Run Terraform apply for range, then run instance setup."""
    request_id = operation.request_id
    range_id = operation.range_id
    user_id = operation.user_id
    range_spec = operation.range_spec
    scenario_artifact = operation.scenario_artifact
    remote_access_capability = operation.remote_access_capability
    publish_status_update(
        request_id=request_id,
        range_id=range_id,
        user_id=user_id,
        new_status=STATUS_PROVISIONING,
    )

    logger.info("Running terraform apply for range...")

    vpn_secret_ops = get_vpn_secret_ops() if remote_access_capability is not None else None
    vpn_preparation = (
        prepare_openvpn_access(
            request_id,
            range_id,
            user_id,
            range_spec,
            remote_access_capability,
            vpn_secret_ops,
        )
        if remote_access_capability is not None and vpn_secret_ops is not None
        else None
    )

    uses_gce = operation.backend == "gce" if operation.backend is not None else is_gce_range_cell_backend()
    spec_subnets = _allocate_range_subnet_cidrs(
        request_id,
        range_id,
        range_spec,
        persist_to_scenario=not uses_gce,
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
        backend=operation.backend,
        remote_access_capability=remote_access_capability,
    )

    # Run the provider-routed apply
    output_data = range_terraform_runner.apply_range(
        request_id,
        provision_variables,
        backend=operation.backend,
        purpose=operation.purpose or InstantiationPurpose.LIVE_FIRE,
    )
    vpn_access_binding = (
        finalize_openvpn_access(
            vpn_preparation,
            verify_openvpn_gateway(output_data.get("vpn_gateway")),
            vpn_secret_ops,
        )
        if vpn_preparation is not None and vpn_secret_ops is not None
        else None
    )

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
        vpn_access_binding=vpn_access_binding,
        operation=OperationRef(request_id=request_id, operation_id=operation.operation_id),
    )

    publish_ready(request_id=request_id, range_id=range_id, user_id=user_id)


def _ensure_range_is_active(request_id: str, range_id: int) -> bool:
    """Return True if the range exists and is not already destroyed."""
    try:
        range_data = get_range_data_by_request_id(request_id)
    except ValueError as e:
        logger.warning("Range not found for request %s, skipping destroy: %s", request_id, e)
        return False
    if range_data.get("status") == STATUS_DESTROYED:
        logger.info("Range %d already destroyed, skipping", range_id)
        return False
    return True


def _run_terraform_destroy(operation: RangeOperation) -> None:
    """Run Terraform destroy for range.

    ``operation.backend`` is the #1666 per-operation ownership binding: teardown
    routes from the backend the range was provisioned on, not the current env
    selector, so a ``gdc -> gce`` deploy-selector flip cannot strand an existing
    GDC range.
    """
    request_id = operation.request_id
    range_id = operation.range_id
    user_id = operation.user_id
    range_spec = operation.range_spec
    scenario_artifact = operation.scenario_artifact
    backend = operation.backend
    remote_access_capability = operation.remote_access_capability
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
            remote_access_capability,
        )
        range_terraform_runner.destroy_range(request_id, variables=destroy_variables, backend=backend)
        _cleanup_openvpn_if_enabled(range_id, request_id)
        terraform_succeeded = True
        logger.info("Cleaning up Terraform state...")
        range_terraform_runner.cleanup_range_state(request_id, backend)
    finally:
        if terraform_succeeded:
            _post_destroy_cleanup(request_id, range_id, operation_id=operation.operation_id)
        _maybe_pause_user_ngfw(user_id, range_id)

    publish_destroyed(request_id=request_id, range_id=range_id, user_id=user_id)
