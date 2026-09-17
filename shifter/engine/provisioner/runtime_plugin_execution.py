"""Consume isolated plans and execute only on the immutable range's guest map."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from uuid import UUID

from shared.runtime_plugin_binding import runtime_plugin_requests
from shifter_adapter_sdk.guest import RUNTIME_VALUES_ENV
from shifter_adapter_sdk.runtime import RuntimeInput, RuntimePlan

from executors.factory import build_guest_execution_context
from provisioner_db import get_db_connection
from provisioner_db_operation_input import RaesOperationRun
from runtime_plugin_values import resolve_runtime_values


class RuntimePluginExecutionError(RuntimeError):
    """Closed error: private scripts and transport diagnostics are never exposed."""


@dataclass(frozen=True)
class GuestPluginPlans:
    requests: tuple[RuntimeInput, ...]
    plans: tuple[RuntimePlan, ...]

    def execute(self, _raes_plan, instances: list[dict]) -> None:
        """Run configure then verify inside apply's existing cleanup boundary."""
        try:
            _execute(self, instances)
        except Exception:
            raise RuntimePluginExecutionError("Runtime plugin guest execution failed") from None


def _read_plan(request: RuntimeInput) -> RuntimePlan | None:
    # A transport projection only: no plugin registry or Engine domain reads.
    with get_db_connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT state, input_digest, result FROM engine_runtime_plugin_invocation "
            "WHERE id = %s AND operation_id = %s",
            (str(request.invocation_id), str(request.operation_id)),
        )
        row = cursor.fetchone()
    if row is None or row[1] != request.digest or row[0] not in {"pending", "planned"}:
        raise RuntimePluginExecutionError("Runtime plugin planning is unavailable")
    if row[0] == "pending":
        return None
    result = RuntimePlan.model_validate(row[2])
    result.authorize(request)
    if result.status != "planned":
        raise RuntimePluginExecutionError("Runtime plugin planning failed")
    return result


def load_guest_plugin_plans(run: RaesOperationRun) -> GuestPluginPlans | None:
    """Wait for validated plans before any provider mutation, with a hard deadline."""
    parsed = run.input
    if parsed.runtime_plugin is None:
        return None
    try:
        if parsed.range_backend not in {"gce", "ec2"}:
            raise ValueError("Unsupported plugin backend")
        requests = runtime_plugin_requests(
            parsed.runtime_plugin,
            parsed.plan,
            UUID(run.operation_id),
            parsed.legacy_range_id,
            backend=parsed.range_backend,
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            plans = tuple(_read_plan(request) for request in requests)
            if all(plan is not None for plan in plans):
                _validate_value_requirements(requests, plans, parsed.access_bindings)
                return GuestPluginPlans(requests, plans)
            time.sleep(2)
    except Exception:
        raise RuntimePluginExecutionError("Runtime plugin planning failed") from None
    raise RuntimePluginExecutionError("Runtime plugin planning timed out")


def _validate_value_requirements(requests, plans, access_bindings) -> None:
    """Reject unavailable participant access before any cloud mutation."""
    ssh_nodes = {binding.target_address for binding in access_bindings if binding.channel == "ssh"}
    for request, plan in zip(requests, plans, strict=True):
        for action in plan.actions:
            for value in action.runtime_values.values():
                if (
                    value.field == "participant_ssh_public_key"
                    and request.targets[value.binding].node_address not in ssh_nodes
                ):
                    raise ValueError("Plugin requires an undeclared participant SSH endpoint")


def _quiet_script(script: str, os_family: str, values_b64: str) -> str:
    """Only exit status is evidence; discard private script output on the guest."""
    if os_family == "windows":
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return (
            f"$env:{RUNTIME_VALUES_ENV} = '{values_b64}'\n"
            f"& powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded} *> $null\nexit $LASTEXITCODE\n"
        )
    encoded = base64.b64encode(script.encode()).decode("ascii")
    return (
        f"{RUNTIME_VALUES_ENV}='{values_b64}' "
        f"bash -euo pipefail -c \"$(printf %s '{encoded}' | base64 -d)\" >/dev/null 2>&1\n"
    )


def _execute(bundle: GuestPluginPlans, instances: list[dict]) -> None:
    outputs = {instance["uuid"]: instance for instance in instances}
    if len(outputs) != len(instances) or len(bundle.requests) != 3 or len(bundle.plans) != 3:
        raise ValueError("Invalid plugin guest coverage")
    # Validate the complete plan set and every target before executing one action.
    prepared_actions = []
    for request, result in zip(bundle.requests, bundle.plans, strict=True):
        result = RuntimePlan.model_validate(result)
        result.authorize(request)
        if result.status != "planned":
            raise ValueError("Plugin planning failed")
        for target in request.targets.values():
            if f"{target.node_address}#0" not in outputs:
                raise ValueError("Plugin guest is unavailable")
        for action in result.actions:
            prepared_actions.append(
                (request.targets[action.binding], action, resolve_runtime_values(action, request, outputs))
            )
    for target, action, values_b64 in prepared_actions:
        execution = build_guest_execution_context(
            outputs[f"{target.node_address}#0"],
            os_type=target.os_family,
            role="raes-node",
        )
        try:
            if not execution.wait_for_ready(timeout_seconds=60):
                raise ValueError("Plugin guest is not ready")
            outcome = execution.executor.run_command(
                execution.target,
                _quiet_script(action.script, target.os_family, values_b64),
                timeout_seconds=action.timeout_seconds,
                document_name=execution.document_name,
            )
            if not outcome.success or outcome.exit_code != 0:
                raise ValueError("Plugin action failed")
        finally:
            execution.close()
