"""Range-wide pause/resume orchestration.

Classifies range instances into lifecycle-execution modes, runs the
per-instance pause/resume operations in parallel, and updates range/instance
status in the database.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from shared.operation_results import ResultStep

from events import (
    STATUS_PAUSED,
    STATUS_READY,
)
from executors.aws_executor import AWSExecutor
from orchestrators.ops_orchestrator import OpsOrchestrator
from plans.range_pause import RangePausePlan
from plans.range_resume import RangeResumePlan
from provisioner_db_appends import OperationRef, append_operation_step_result

logger = logging.getLogger(__name__)

# ADR-043 phase 4 (#1836): the Engine applier is the authoritative writer for
# this family. The provisioner reports closed, per-step results instead of
# writing engine_instance / mission_control_range and enqueuing its own events.
_INSTANCE_STEP_BY_STATUS = {
    STATUS_PAUSED: ResultStep.RANGE_INSTANCES_PAUSED,
    STATUS_READY: ResultStep.RANGE_INSTANCES_READY,
}

_OPERATION_BY_INSTANCE_STEP = {
    ResultStep.RANGE_INSTANCES_PAUSED: "pause",
    ResultStep.RANGE_INSTANCES_READY: "resume",
}

_TERMINAL_STEP_BY_OPERATION = {
    "pause": ResultStep.RANGE_TERMINAL_PAUSED,
    "resume": ResultStep.RANGE_TERMINAL_READY,
}

_TERMINAL_STATUS_BY_OPERATION = {
    "pause": STATUS_PAUSED,
    "resume": STATUS_READY,
}


def _report_terminal_success(ref: OperationRef, operation: str) -> None:
    """Report the operation's terminal success to the Engine applier."""
    append_operation_step_result(
        ref,
        resource="range",
        operation=operation,
        step=_TERMINAL_STEP_BY_OPERATION[operation],
        result_payload={"status": _TERMINAL_STATUS_BY_OPERATION[operation]},
    )


def _report_terminal_failure(ref: OperationRef, operation: str, diagnostic: str) -> None:
    """Report the operation's terminal failure with a closed reason code.

    Only the authored reason code reaches the Range's user-visible error text;
    the diagnostic is bounded and stays in the result payload.
    """
    append_operation_step_result(
        ref,
        resource="range",
        operation=operation,
        step=ResultStep.RANGE_TERMINAL_FAILED,
        result_payload={"reason_code": "cloud_operation_failed", "diagnostic": diagnostic[:512]},
    )


_GCP_RANGE_LIFECYCLE_NOT_IMPLEMENTED = (
    "GCP range pause/resume is not implemented yet. "
    "Pod-backed assets do not preserve runtime state across pause/resume, "
    "so the GCP lifecycle path is intentionally disabled until parity work is complete."
)


# (cloud_provider, asset_type) -> operation_mode for non-AWS lifecycle targets.
_GCP_OPERATION_MODES = {
    ("gcp", "gce_vm"): "gce_vm",
    ("gcp", "vm_runtime_vm"): "gdc_vm_runtime",
    ("gcp", "scenario_pod"): "gdc_scenario_pod",
}


def _build_aws_lifecycle_entry(
    entry: dict[str, object], state_dict: dict[str, object], uuid: object, role: str
) -> dict[str, object] | None:
    """Finalize an AWS lifecycle entry, or None when the instance lacks an aws_instance_id."""
    aws_instance_id = state_dict.get("aws_instance_id")
    if not aws_instance_id:
        logger.warning(
            "Instance %s (role=%s) missing aws_instance_id in state, skipping",
            uuid,
            role,
        )
        return None
    entry["operation_mode"] = "aws"
    entry["aws_instance_id"] = aws_instance_id
    return entry


def _build_range_lifecycle_entry(
    request_id: str,
    uuid: object,
    state: object,
    role: str,
    name: str | None,
) -> dict[str, object] | None:
    """Build the lifecycle-operation entry for one instance, or None if it's an unmapped AWS asset."""
    state_dict = state if isinstance(state, dict) else {}
    cloud_provider = str(state_dict.get("cloud_provider", "aws")).strip().lower() or "aws"
    asset_type = str(state_dict.get("asset_type", "vm_runtime_vm")).strip() or "vm_runtime_vm"
    entry: dict[str, object] = {
        "uuid": str(uuid),
        "name": name or "",
        "role": role,
        "cloud_provider": cloud_provider,
        "asset_type": asset_type,
        "state": state_dict,
    }

    if cloud_provider == "aws":
        return _build_aws_lifecycle_entry(entry, state_dict, uuid, role)

    operation_mode = _GCP_OPERATION_MODES.get((cloud_provider, asset_type))
    if operation_mode is None:
        raise ValueError(
            "Unsupported range lifecycle target "
            f"for request {request_id}: cloud_provider={cloud_provider!r} asset_type={asset_type!r}"
        )
    entry["operation_mode"] = operation_mode
    return entry


def get_range_instance_ids(request_id: str) -> list[dict[str, Any]]:
    """Get all range assets for pause/resume operations.

    Queries engine_instance records for the given request and extracts
    provider/runtime-specific lifecycle targets from the state JSON field.

    Args:
        request_id: UUID string of the Request.

    Returns:
        List of dicts describing how each asset should participate in
        lifecycle operations.

    Raises:
        ValueError: If no instances are found or an instance cannot be mapped
            onto a supported lifecycle mode.
    """
    logger.info("get_range_instance_ids: request_id=%s", request_id)

    # Late-bound call to ``range_ops.get_db_connection`` so test patches
    # applied at the package level still apply here.
    import range_ops as _pkg

    with _pkg.get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.uuid, i.state, i.role, i.name
            FROM engine_instance i
            JOIN engine_request r ON i.request_id = r.id
            WHERE r.request_id = %s
              AND i.status IN (%s, %s)
            """,
            (request_id, STATUS_READY, STATUS_PAUSED),
        )
        rows = cur.fetchall()

    if not rows:
        raise ValueError(f"No instances found for request: {request_id}")

    instances = []
    for uuid, state, role, name in rows:
        entry = _build_range_lifecycle_entry(request_id, uuid, state, role, name)
        if entry is not None:
            instances.append(entry)

    if not instances:
        raise ValueError(f"No lifecycle-managed assets found for request: {request_id}")

    logger.info(
        "get_range_instance_ids: found %d instances for request_id=%s",
        len(instances),
        request_id,
    )
    return instances


def _execute_instance_operation(
    executor: AWSExecutor | None,
    orchestrator: OpsOrchestrator | None,
    plan: RangePausePlan | RangeResumePlan | None,
    instance: dict[str, Any],
    *,
    operation: str,
) -> tuple[str, bool, str | None]:
    """Execute pause/resume operation on a single instance.

    Args:
        executor: AWSExecutor instance for AWS-backed assets.
        orchestrator: OpsOrchestrator instance for AWS-backed assets.
        plan: Plan to execute for AWS-backed assets.
        instance: Dict describing the asset and its lifecycle mode.
        operation: Operation name ("pause" or "resume").

    Returns:
        Tuple of (uuid, success, error_message).
    """
    uuid = instance["uuid"]
    mode = instance["operation_mode"]

    try:
        if mode == "gdc_vm_runtime":
            raise NotImplementedError(_GCP_RANGE_LIFECYCLE_NOT_IMPLEMENTED)

        if mode == "gdc_scenario_pod":
            raise NotImplementedError(_GCP_RANGE_LIFECYCLE_NOT_IMPLEMENTED)

        if mode == "gce_vm":
            raise NotImplementedError("GCE range pause/resume is not implemented yet.")

        if mode != "aws" or executor is None or orchestrator is None or plan is None:
            raise RuntimeError(f"Unsupported lifecycle execution mode {mode!r} for uuid={uuid}")

        aws_instance_id = instance["aws_instance_id"]
        context = plan.get_context(aws_instance_id)
        result = orchestrator.orchestrate(aws_instance_id, plan, context)

        if result.success:
            logger.info(
                "Operation succeeded for instance %s (uuid=%s)",
                aws_instance_id,
                uuid,
            )
            return (uuid, True, None)
        else:
            error_msg = f"Operation failed: {result.error}"
            logger.error(
                "Operation failed for instance %s (uuid=%s): %s",
                aws_instance_id,
                uuid,
                result.error,
            )
            return (uuid, False, error_msg)

    except Exception as e:
        error_msg = str(e)
        logger.exception("Exception during %s for uuid=%s mode=%s", operation, uuid, mode)
        return (uuid, False, error_msg)


def run_range_pause(request_id: str, *, operation_id: str | None = None) -> None:
    """Pause all instances in a range.

    Stops all EC2 instances belonging to the range in parallel, waits for them to
    reach paused state, then reports the outcome to the Engine result inbox. The
    applier — not this process — writes the resulting domain state (ADR-043).

    Args:
        request_id: UUID string of the Request.
        operation_id: ADR-043 canonical operation generation; absent on local-dev
            runs, in which case no result is appended.

    Raises:
        ValueError: If request not found or no instances.
        Exception: If pause operation fails.
    """
    ref = OperationRef(request_id=request_id, operation_id=operation_id)
    logger.info("run_range_pause: starting request_id=%s", request_id)

    # Late-bound calls to package-level names so test patches applied at
    # the package level still apply here.
    import range_ops as _pkg

    # Get range data for status updates and events
    range_data = _pkg.get_range_data_by_request_id(request_id)
    current_status = range_data["status"]

    # Idempotent: if already paused, return success
    if current_status == STATUS_PAUSED:
        logger.info("run_range_pause: range already paused, no-op request_id=%s", request_id)
        return

    # Get all instances to pause
    instances = _pkg.get_range_instance_ids(request_id)

    # Create AWS lifecycle helpers lazily; GCP-only ranges do not need them.
    has_aws_assets = any(instance["operation_mode"] == "aws" for instance in instances)
    executor = _pkg.AWSExecutor() if has_aws_assets else None
    orchestrator = _pkg.OpsOrchestrator(executor) if executor is not None else None
    plan = RangePausePlan() if executor is not None else None

    # Execute stop operations in parallel
    results = []
    with ThreadPoolExecutor(max_workers=len(instances)) as pool:
        futures = {
            pool.submit(
                _execute_instance_operation,
                executor,
                orchestrator,
                plan,
                instance,
                operation="pause",
            ): instance
            for instance in instances
        }

        for future in as_completed(futures):
            instance = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.exception(
                    "Unexpected error pausing instance uuid=%s",
                    instance["uuid"],
                )
                results.append((instance["uuid"], False, str(e)))

    # Check for failures
    failures = [(uuid, err) for uuid, success, err in results if not success]

    if failures:
        error_msg = f"Failed to pause {len(failures)}/{len(instances)} instances"
        logger.error("run_range_pause: %s", error_msg)
        _report_terminal_failure(ref, "pause", error_msg)
        raise RuntimeError(error_msg)

    # Report instance outcomes for the closed set this operation handled.
    _pkg._update_instance_statuses(request_id, STATUS_PAUSED, instances=instances, ref=ref)

    # Cascade: pause NGFW if no other ranges need it (before reporting paused)
    try:
        _pkg.pause_ngfw_for_range(request_id, ref=ref)
    except Exception as e:
        # Non-fatal: log and continue - range instances are already paused
        logger.warning(
            "run_range_pause: NGFW pause failed (non-fatal): %s request_id=%s",
            str(e),
            request_id,
        )

    _report_terminal_success(ref, "pause")

    logger.info(
        "run_range_pause: completed request_id=%s, paused %d instances",
        request_id,
        len(instances),
    )


def run_range_resume(request_id: str, *, operation_id: str | None = None) -> None:
    """Resume all instances in a range.

    Starts all EC2 instances belonging to the range in parallel,
    waits for them to reach running state, then updates the range status.

    Args:
        request_id: UUID string of the Request.

    Raises:
        ValueError: If request not found or no instances.
        Exception: If resume operation fails.
    """
    logger.info("run_range_resume: starting request_id=%s", request_id)

    # Late-bound calls to package-level names so test patches applied at
    # the package level still apply here.
    import range_ops as _pkg

    ref = OperationRef(request_id=request_id, operation_id=operation_id)

    # Get range data for status updates and events
    range_data = _pkg.get_range_data_by_request_id(request_id)
    current_status = range_data["status"]

    # Idempotent: if already ready, return success
    if current_status == STATUS_READY:
        logger.info("run_range_resume: range already ready, no-op request_id=%s", request_id)
        return

    # Cascade: ensure NGFW is running before resuming range instances
    try:
        _pkg.ensure_ngfw_running(request_id, ref=ref)
    except Exception as e:
        # Fatal: range cannot resume without NGFW
        error_msg = f"Failed to start NGFW: {e}"
        logger.exception("run_range_resume: %s request_id=%s", error_msg, request_id)
        _report_terminal_failure(ref, "resume", error_msg)
        raise RuntimeError(error_msg) from e

    # Get all instances to resume
    instances = _pkg.get_range_instance_ids(request_id)

    has_aws_assets = any(instance["operation_mode"] == "aws" for instance in instances)
    executor = _pkg.AWSExecutor() if has_aws_assets else None
    orchestrator = _pkg.OpsOrchestrator(executor) if executor is not None else None
    plan = RangeResumePlan() if executor is not None else None

    # Execute start operations in parallel
    results = []
    with ThreadPoolExecutor(max_workers=len(instances)) as pool:
        futures = {
            pool.submit(
                _execute_instance_operation,
                executor,
                orchestrator,
                plan,
                instance,
                operation="resume",
            ): instance
            for instance in instances
        }

        for future in as_completed(futures):
            instance = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.exception(
                    "Unexpected error resuming instance uuid=%s",
                    instance["uuid"],
                )
                results.append((instance["uuid"], False, str(e)))

    # Check for failures
    failures = [(uuid, err) for uuid, success, err in results if not success]

    if failures:
        error_msg = f"Failed to resume {len(failures)}/{len(instances)} instances"
        logger.error("run_range_resume: %s", error_msg)
        _report_terminal_failure(ref, "resume", error_msg)
        raise RuntimeError(error_msg)

    # Report instance outcomes for the closed set this operation handled.
    _pkg._update_instance_statuses(request_id, STATUS_READY, instances=instances, ref=ref)

    _report_terminal_success(ref, "resume")

    logger.info(
        "run_range_resume: completed request_id=%s, resumed %d instances",
        request_id,
        len(instances),
    )


def _update_instance_statuses(
    request_id: str,
    status: str,
    *,
    instances: list[dict[str, Any]] | None = None,
    ref: OperationRef | None = None,
) -> None:
    """Report the range's instance statuses to the Engine result inbox.

    ADR-043 phase 4 (#1836): this no longer writes ``engine_instance`` directly.
    The Engine applier is the authoritative writer; the provisioner reports the
    outcome for the closed set of instance UUIDs this operation actually
    handled, rather than blanket-updating every instance for the request.

    Args:
        request_id: UUID string of the Request.
        status: New status value ('paused' or 'ready').
        instances: The instances this operation handled, as returned by
            ``get_range_instance_ids``. Required to name the closed UUID set.
        ref: ADR-043 operation identity; the append is skipped without one.
    """
    step = _INSTANCE_STEP_BY_STATUS.get(status)
    if step is None:
        raise ValueError(f"no result step declared for instance status '{status}'")

    outcomes = [{"instance_uuid": str(instance["uuid"]), "status": status} for instance in (instances or [])]
    if not outcomes:
        logger.debug("_update_instance_statuses: no instances to report for request_id=%s", request_id)
        return

    append_operation_step_result(
        ref if ref is not None else OperationRef(request_id=request_id),
        resource="range",
        operation=_OPERATION_BY_INSTANCE_STEP[step],
        step=step,
        result_payload={"instances": outcomes},
    )
    logger.debug(
        "_update_instance_statuses: reported %d instances as status=%s",
        len(outcomes),
        status,
    )
