"""Orchestrate bounded ACES Active Directory and SPN realization."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aces_plan import AcesPlan
from executors.base import Executor
from executors.factory import GuestExecutionContext, build_guest_execution_context
from gcp_guest_secrets import (
    delete_aces_domain_account_secret,
    delete_aces_domain_authority_secret,
    delete_aces_domain_dsrm_secret,
    ensure_aces_domain_account_password_secret,
    ensure_aces_domain_authority_secret,
    ensure_aces_domain_dsrm_secret,
)
from orchestrators.setup_orchestrator import SetupOrchestrator
from plans.aces_active_directory import (
    AcesDomainAccountPlan,
    AcesDomainControllerPlan,
    AcesDomainControllerVerificationPlan,
    AcesDomainMemberPlan,
    AcesDomainMemberStatePlan,
    AcesDomainOfflineJoinProvisionPlan,
)
from plans.base import SetupPlan


class AcesActiveDirectoryError(RuntimeError):
    """Value-free failure at the ACES directory realization boundary."""


_MACHINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")
_OFFLINE_JOIN_BLOB_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class AcesDirectorySecretOps:
    ensure_dsrm: Callable[[int, str], tuple[str, str]]
    ensure_authority: Callable[[int, str, str], tuple[str, str]]
    ensure_account: Callable[[int, str, str, str], tuple[str, str]]
    delete_dsrm: Callable[[int, str], None]
    delete_authority: Callable[[int, str], None]
    delete_account: Callable[[int, str, str], None]
    execution_builder: Callable[..., GuestExecutionContext] = build_guest_execution_context
    orchestrator_factory: Callable[[Executor], SetupOrchestrator] = SetupOrchestrator


def default_directory_secret_ops() -> AcesDirectorySecretOps:
    return AcesDirectorySecretOps(
        ensure_dsrm=ensure_aces_domain_dsrm_secret,
        ensure_authority=ensure_aces_domain_authority_secret,
        ensure_account=ensure_aces_domain_account_password_secret,
        delete_dsrm=delete_aces_domain_dsrm_secret,
        delete_authority=delete_aces_domain_authority_secret,
        delete_account=delete_aces_domain_account_secret,
    )


def _run(execution: GuestExecutionContext, plan: SetupPlan, secret_ops: AcesDirectorySecretOps) -> object:
    result = secret_ops.orchestrator_factory(execution.executor).orchestrate(
        execution.target,
        plan,
        plan.get_context({}),
        execution.document_name,
    )
    if not result.success:
        raise AcesActiveDirectoryError("ACES directory setup plan failed")
    return result


def _promotion_was_applied(result: object) -> bool:
    output = "\n".join(str(getattr(step, "stdout", "")) for step in getattr(result, "step_results", ()))
    applied = "ACES_AD_PROMOTION_APPLIED" in output
    verified = "ACES_AD_PROMOTION_VERIFIED" in output
    if applied == verified:
        raise AcesActiveDirectoryError("ACES directory promotion evidence is invalid")
    return applied


def _member_join_state(result: object) -> tuple[str, bool]:
    """Return the bounded local machine name and whether it is already joined."""
    output = "\n".join(str(getattr(step, "stdout", "")) for step in getattr(result, "step_results", ()))
    matches: list[tuple[str, bool]] = []
    for line in output.splitlines():
        if line.startswith("ACES_AD_MEMBER_JOIN_REQUIRED:"):
            matches.append((line.partition(":")[2], False))
        elif line.startswith("ACES_AD_MEMBER_ALREADY_JOINED:"):
            matches.append((line.partition(":")[2], True))
    if len(matches) != 1:
        raise AcesActiveDirectoryError("ACES directory member state evidence is invalid")
    encoded_name, joined = matches[0]
    try:
        machine_name = base64.b64decode(encoded_name, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise AcesActiveDirectoryError("ACES directory member identity is invalid") from None
    if not _MACHINE_NAME.fullmatch(machine_name):
        raise AcesActiveDirectoryError("ACES directory member identity is invalid")
    return machine_name, joined


def _offline_join_blob(execution: GuestExecutionContext, dns_name: str, machine_name: str) -> str:
    """Create an ODJ package without sending its machine credential through setup logs."""
    plan = AcesDomainOfflineJoinProvisionPlan(dns_name=dns_name, machine_name=machine_name)
    step = plan.steps[0]
    result = execution.executor.run_command(
        execution.target,
        step.script,
        timeout_seconds=step.timeout_seconds,
        document_name=execution.document_name,
        stdin_input=step.stdin_input,
    )
    if not result.success:
        raise AcesActiveDirectoryError("ACES directory offline join provisioning failed")
    encoded_blob = result.stdout.strip()
    if not encoded_blob or len(encoded_blob) > _OFFLINE_JOIN_BLOB_LIMIT:
        raise AcesActiveDirectoryError("ACES directory offline join evidence is invalid")
    try:
        decoded_blob = base64.b64decode(encoded_blob, validate=True)
    except binascii.Error:
        raise AcesActiveDirectoryError("ACES directory offline join evidence is invalid") from None
    if not decoded_blob:
        raise AcesActiveDirectoryError("ACES directory offline join evidence is invalid")
    return encoded_blob


def _wait_for_ready(execution: GuestExecutionContext, timeout_seconds: int) -> None:
    if execution.wait_for_ready(timeout_seconds=timeout_seconds) is False:
        raise AcesActiveDirectoryError("ACES directory guest did not become ready")


def _authority_output(controller_output: dict[str, Any], authority_username: str) -> dict[str, Any]:
    output = dict(controller_output)
    output["gcp_host_ssh_username"] = authority_username
    output["ssh_username"] = authority_username
    return output


def _output(instance_outputs: dict[str, dict[str, Any]], instance_key: str) -> dict[str, Any]:
    try:
        return instance_outputs[instance_key]
    except KeyError:
        raise AcesActiveDirectoryError("ACES directory instance output is missing") from None


def realize_aces_active_directory(
    *,
    range_id: int,
    aces_plan: AcesPlan,
    instance_outputs: list[dict[str, Any]],
    secret_ops: AcesDirectorySecretOps | None = None,
) -> None:
    """Realize every admitted domain before range apply may report success."""
    resolved_ops = secret_ops or default_directory_secret_ops()
    outputs = {str(output.get("uuid", "")): output for output in instance_outputs}
    accounts = {account.address: account for account in aces_plan.accounts}
    nodes = {node.address: node for node in aces_plan.nodes}

    for domain in aces_plan.domains:
        controller_execution: GuestExecutionContext | None = None
        member_executions: list[GuestExecutionContext] = []
        try:
            controller_address = domain.controller_addresses[0]
            controller_output = _output(outputs, f"{controller_address}#0")
            authority = accounts[domain.authority_account_address]
            _dsrm_ref, dsrm_password = resolved_ops.ensure_dsrm(range_id, domain.domain_id)
            _authority_ref, authority_password = resolved_ops.ensure_authority(
                range_id, domain.domain_id, authority.password_strength
            )
            controller_execution = resolved_ops.execution_builder(
                controller_output,
                os_type="windows",
                role="aces-node",
            )
            try:
                _wait_for_ready(controller_execution, 60)
            except Exception:
                controller_execution.close()
                controller_execution = resolved_ops.execution_builder(
                    _authority_output(controller_output, authority.username),
                    os_type="windows",
                    role="aces-node",
                )
                _wait_for_ready(controller_execution, 600)
            promotion_result = _run(
                controller_execution,
                AcesDomainControllerPlan(
                    dns_name=domain.dns_name,
                    netbios_name=domain.netbios_name,
                    authority_username=authority.username,
                    dsrm_password=dsrm_password,
                    authority_password=authority_password,
                ),
                resolved_ops,
            )
            promotion_applied = _promotion_was_applied(promotion_result)
            controller_execution.close()
            controller_execution = resolved_ops.execution_builder(
                _authority_output(controller_output, authority.username),
                os_type="windows",
                role="aces-node",
            )
            if promotion_applied:
                controller_execution.executor.reboot_and_wait(
                    controller_execution.target,
                    timeout_seconds=1200,
                    document_name=controller_execution.document_name,
                )
            _wait_for_ready(controller_execution, 600)
            _run(
                controller_execution,
                AcesDomainControllerVerificationPlan(
                    dns_name=domain.dns_name,
                    netbios_name=domain.netbios_name,
                    authority_username=authority.username,
                    authority_password=authority_password,
                ),
                resolved_ops,
            )

            controller_ip = str(controller_output.get("private_ip", ""))
            if not controller_ip:
                raise AcesActiveDirectoryError("ACES directory controller address is missing")
            for member_address in domain.member_addresses:
                member = nodes[member_address]
                for index in range(member.count):
                    execution = resolved_ops.execution_builder(
                        _output(outputs, f"{member_address}#{index}"),
                        os_type="windows",
                        role="aces-node",
                    )
                    member_executions.append(execution)
                    _wait_for_ready(execution, 600)
                    state_result = _run(
                        execution,
                        AcesDomainMemberStatePlan(
                            dns_name=domain.dns_name,
                            controller_ip=controller_ip,
                        ),
                        resolved_ops,
                    )
                    machine_name, joined = _member_join_state(state_result)
                    if not joined:
                        blob = _offline_join_blob(controller_execution, domain.dns_name, machine_name)
                        _run(
                            execution,
                            AcesDomainMemberPlan(
                                dns_name=domain.dns_name,
                                controller_ip=controller_ip,
                                offline_join_blob=blob,
                            ),
                            resolved_ops,
                        )

            for account in (item for item in aces_plan.accounts if item.domain_ref == domain.domain_id):
                _account_ref, password = resolved_ops.ensure_account(
                    range_id,
                    domain.domain_id,
                    account.address,
                    account.password_strength,
                )
                _run(
                    controller_execution,
                    AcesDomainAccountPlan(
                        dns_name=domain.dns_name,
                        username=account.username,
                        password=password,
                        spn=account.spn,
                    ),
                    resolved_ops,
                )
        except AcesActiveDirectoryError:
            raise
        except Exception:
            raise AcesActiveDirectoryError("ACES directory realization failed") from None
        finally:
            for execution in member_executions:
                execution.close()
            if controller_execution is not None:
                controller_execution.close()


def delete_aces_directory_secrets(
    range_id: int,
    aces_plan: AcesPlan,
    secret_ops: AcesDirectorySecretOps | None = None,
) -> None:
    """Delete every deterministic directory secret reconstructible from the plan."""
    resolved_ops = secret_ops or default_directory_secret_ops()
    for domain in aces_plan.domains:
        for account in (item for item in aces_plan.accounts if item.domain_ref == domain.domain_id):
            resolved_ops.delete_account(range_id, domain.domain_id, account.address)
        resolved_ops.delete_authority(range_id, domain.domain_id)
        resolved_ops.delete_dsrm(range_id, domain.domain_id)
