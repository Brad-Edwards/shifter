"""Optional Caldera setup orchestration for provisioned range VMs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from executors.factory import GuestExecutionContext, build_guest_execution_context
from instance_setup import _setup_ready_timeout
from orchestrators.setup_orchestrator import SetupError, SetupOrchestrator
from plans.base import SetupPlan
from plans.caldera import CalderaServerPlan, LinuxSandcatAgentPlan, WindowsSandcatAgentPlan

logger = logging.getLogger(__name__)

_DEFAULT_CALLBACK_PORT = 8888
_DEFAULT_TARGET_ROLES = ["victim", "dc"]
_DEFAULT_WINDOWS_DEFENDER_MODE = "path_exclusion"
_DEFAULT_SERVER_WORKING_DIRECTORY = "/opt/caldera"
_DEFAULT_SERVER_START_COMMAND = "/usr/local/bin/start-caldera"
_ERR_INVALID_CALLBACK_PORT = "Invalid Caldera callback port in range spec"
_WINDOWS_OS_TYPES = {"windows"}
_SUPPORTED_TARGET_ROLES = frozenset({"victim", "dc"})
_SUPPORTED_WINDOWS_DEFENDER_MODES = frozenset({"path_exclusion", "disable_realtime"})


@dataclass(frozen=True)
class _CalderaRuntimeProfile:
    """Provisioner-local Caldera runtime profile derived from the range spec."""

    enabled: bool = False
    callback_port: int = _DEFAULT_CALLBACK_PORT
    target_roles: list[str] = field(default_factory=lambda: list(_DEFAULT_TARGET_ROLES))
    windows_defender_mode: str = _DEFAULT_WINDOWS_DEFENDER_MODE
    server_working_directory: str = _DEFAULT_SERVER_WORKING_DIRECTORY
    server_start_command: str = _DEFAULT_SERVER_START_COMMAND


def _normalize_caldera_profile(range_spec: dict[str, Any]) -> _CalderaRuntimeProfile:
    """Return a validated Caldera runtime profile from a raw range spec."""

    raw = range_spec.get("caldera", False)
    if raw is None:
        return _CalderaRuntimeProfile()
    if isinstance(raw, bool):
        return _CalderaRuntimeProfile(enabled=raw)
    if not isinstance(raw, dict):
        raise SetupError("Invalid Caldera profile in range spec")

    port = _parse_callback_port(raw.get("callback_port", _DEFAULT_CALLBACK_PORT))
    target_roles = _parse_target_roles(raw.get("target_roles", _DEFAULT_TARGET_ROLES))
    defender_mode = str(raw.get("windows_defender_mode", _DEFAULT_WINDOWS_DEFENDER_MODE) or "")
    if defender_mode not in _SUPPORTED_WINDOWS_DEFENDER_MODES:
        raise SetupError("Invalid Caldera Windows Defender mode in range spec")

    return _CalderaRuntimeProfile(
        enabled=bool(raw.get("enabled", False)),
        callback_port=port,
        target_roles=target_roles,
        windows_defender_mode=defender_mode,
        server_working_directory=_required_string(
            raw.get("server_working_directory", _DEFAULT_SERVER_WORKING_DIRECTORY),
            "Caldera server working directory",
        ),
        server_start_command=_required_string(
            raw.get("server_start_command", _DEFAULT_SERVER_START_COMMAND),
            "Caldera server start command",
        ),
    )


def _parse_callback_port(raw: object) -> int:
    """Parse and validate the Caldera callback port."""

    if isinstance(raw, bool):
        raise SetupError(_ERR_INVALID_CALLBACK_PORT)
    if isinstance(raw, int):
        port = raw
    elif isinstance(raw, str):
        try:
            port = int(raw)
        except ValueError as exc:
            raise SetupError(_ERR_INVALID_CALLBACK_PORT) from exc
    else:
        raise SetupError(_ERR_INVALID_CALLBACK_PORT)
    if port < 1 or port > 65535:
        raise SetupError(_ERR_INVALID_CALLBACK_PORT)
    return port


def _parse_target_roles(raw: object) -> list[str]:
    """Parse the target roles that should receive sandcat agents."""

    if not isinstance(raw, list) or not raw:
        raise SetupError("Caldera target_roles must be a non-empty list")
    roles = [str(role) for role in raw]
    if len(set(roles)) != len(roles):
        raise SetupError("Caldera target_roles must not contain duplicates")
    unsupported = set(roles) - _SUPPORTED_TARGET_ROLES
    if unsupported:
        raise SetupError("Caldera target_roles must be limited to victim and dc")
    return roles


def _required_string(raw: object, label: str) -> str:
    """Return a stripped string value or raise a setup error."""

    value = str(raw or "").strip()
    if not value:
        raise SetupError(f"{label} is required")
    return value


def _is_vm_instance(instance: dict[str, Any]) -> bool:
    """Return whether an asset should be treated as a guest VM."""

    return instance.get("asset_type", "vm_runtime_vm") != "scenario_pod"


def _select_caldera_attacker(vm_instances: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the single Kali attacker that hosts the Caldera server."""

    attackers = [instance for instance in vm_instances if instance.get("role") == "attacker"]
    kali_attackers = [instance for instance in attackers if instance.get("os") == "kali"]
    if len(attackers) != 1 or len(kali_attackers) != 1:
        raise SetupError("Caldera setup requires exactly one Kali attacker VM")
    attacker = kali_attackers[0]
    if not str(attacker.get("private_ip", "") or "").strip():
        raise SetupError("Caldera setup requires the Kali attacker private_ip")
    return attacker


def _select_caldera_targets(
    vm_instances: list[dict[str, Any]],
    attacker: dict[str, Any],
    target_roles: list[str],
) -> list[dict[str, Any]]:
    """Select non-attacker VMs whose roles are configured as Caldera targets."""

    attacker_id = attacker.get("instance_id")
    return [
        instance
        for instance in vm_instances
        if instance.get("instance_id") != attacker_id and str(instance.get("role", "")) in target_roles
    ]


def _wait_for_guest_or_raise(execution: GuestExecutionContext) -> None:
    """Wait for guest command transport readiness or raise a setup error."""

    if not execution.wait_for_ready(timeout_seconds=_setup_ready_timeout(execution.transport_name)):
        raise SetupError(
            f"Caldera setup target {execution.target} did not become ready over {execution.transport_name}"
        )


def _run_setup_plan_or_raise(
    instance_data: dict[str, Any],
    *,
    os_type: str,
    role: str,
    plan: SetupPlan,
    context: dict[str, Any],
    failure_prefix: str,
) -> None:
    """Run one setup plan against one guest and normalize failures."""

    execution = build_guest_execution_context(instance_data, os_type=os_type, role=role)
    orchestrator = SetupOrchestrator(executor=execution.executor)
    try:
        logger.info(
            "Waiting for %s connectivity on %s for Caldera setup...",
            execution.transport_name,
            execution.target,
        )
        _wait_for_guest_or_raise(execution)
        result = orchestrator.orchestrate(execution.target, plan, context, document_name=execution.document_name)
        if not result.success:
            raise SetupError(f"{failure_prefix}: {result.error}")
    finally:
        execution.close()


def _start_caldera_server(attacker: dict[str, Any], profile: _CalderaRuntimeProfile) -> str:
    """Start the Caldera server on the attacker and return its callback URL."""

    plan = CalderaServerPlan()
    context = plan.get_context(
        {
            "callback_port": profile.callback_port,
            "server_working_directory": profile.server_working_directory,
            "server_start_command": profile.server_start_command,
        }
    )
    _run_setup_plan_or_raise(
        attacker,
        os_type="kali",
        role="attacker",
        plan=plan,
        context=context,
        failure_prefix="Caldera server setup failed",
    )
    attacker_ip = str(attacker["private_ip"]).strip()
    return f"http://{attacker_ip}:{profile.callback_port}"  # NOSONAR - private range; Sandcat uses HTTP.


def _deploy_sandcat(target: dict[str, Any], server_url: str, profile: _CalderaRuntimeProfile) -> None:
    """Deploy the appropriate sandcat agent to a target VM."""

    os_type = str(target.get("os", "") or "")
    role = str(target.get("role", "") or "")
    if os_type in _WINDOWS_OS_TYPES:
        plan: SetupPlan = WindowsSandcatAgentPlan()
        context = plan.get_context(
            {
                "caldera_server_url": server_url,
                "windows_defender_mode": profile.windows_defender_mode,
            }
        )
    else:
        plan = LinuxSandcatAgentPlan()
        context = plan.get_context({"caldera_server_url": server_url})

    _run_setup_plan_or_raise(
        target,
        os_type=os_type,
        role=role,
        plan=plan,
        context=context,
        failure_prefix=f"Caldera sandcat setup failed for {target.get('instance_id', 'unknown')}",
    )


def run_caldera_setup_if_enabled(instances_output: list[dict[str, Any]], range_spec: dict[str, Any]) -> None:
    """Start Caldera and deploy sandcat when the range spec opts in."""
    profile = _normalize_caldera_profile(range_spec)
    if not profile.enabled:
        logger.info("Caldera setup disabled for this range")
        return

    vm_instances = [instance for instance in instances_output if _is_vm_instance(instance)]
    attacker = _select_caldera_attacker(vm_instances)
    targets = _select_caldera_targets(vm_instances, attacker, profile.target_roles)

    logger.info(
        "Caldera setup enabled: attacker=%s targets=%d port=%d",
        attacker.get("instance_id", ""),
        len(targets),
        profile.callback_port,
    )
    server_url = _start_caldera_server(attacker, profile)
    for target in targets:
        _deploy_sandcat(target, server_url, profile)
    logger.info("Caldera setup complete")
