"""Realize RAES-authored account credentials over the management SSH channel."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from executors.base import Executor
from executors.factory import GuestExecutionContext, build_guest_execution_context
from gcp_guest_secrets import (
    delete_raes_account_secret,
    ensure_raes_account_password_secret,
    ensure_raes_account_public_key_secret,
)
from orchestrators.setup_orchestrator import SetupOrchestrator
from plans.set_authorized_key import SetAuthorizedKeyPlan
from plans.set_local_password import SetLocalPasswordPlan
from raes_plan import RaesPlanAccount


class RaesAccountCredentialError(RuntimeError):
    """Bounded failure for one authored-account credential realization."""


@dataclass(frozen=True)
class RaesAccountCredentialOps:
    """Injectable Secret Manager operations for authored-account credentials."""

    ensure_password: Callable[[int, str, str, str], tuple[str, str]]
    ensure_public_key: Callable[[int, str, str], tuple[str, str]]
    delete: Callable[[int, str, str, str], None]
    execution_builder: Callable[..., GuestExecutionContext] = build_guest_execution_context
    orchestrator_factory: Callable[[Executor], SetupOrchestrator] = SetupOrchestrator


def default_account_credential_ops() -> RaesAccountCredentialOps:
    """Return the production authored-account Secret Manager bindings."""
    return RaesAccountCredentialOps(
        ensure_password=ensure_raes_account_password_secret,
        ensure_public_key=ensure_raes_account_public_key_secret,
        delete=delete_raes_account_secret,
    )


def _run_password_strategy(
    orchestrator: SetupOrchestrator,
    execution: GuestExecutionContext,
    range_id: int,
    instance_key: str,
    platform: str,
    account: RaesPlanAccount,
    secret_ops: RaesAccountCredentialOps,
) -> str:
    """Install one authored account's password, returning its secret reference."""
    secret_ref, password = secret_ops.ensure_password(
        range_id, instance_key, account.username, account.password_strength
    )
    plan = SetLocalPasswordPlan(platform=platform)
    context = plan.get_context({"rdp_username": account.username, "rdp_password": password})
    result = orchestrator.orchestrate(execution.target, plan, context, execution.document_name)
    verification = result.verification_result
    if not result.success or verification is None or not verification.success:
        raise RuntimeError("password setup plan did not complete")
    return secret_ref


def _run_public_key_strategy(
    orchestrator: SetupOrchestrator,
    execution: GuestExecutionContext,
    range_id: int,
    instance_key: str,
    platform: str,
    account: RaesPlanAccount,
    secret_ops: RaesAccountCredentialOps,
) -> str:
    """Install one authored account's public key, returning its secret reference."""
    secret_ref, public_key = secret_ops.ensure_public_key(range_id, instance_key, account.username)
    plan = SetAuthorizedKeyPlan(platform=platform)
    context = plan.get_context({"account_username": account.username, "account_public_key": public_key})
    result = orchestrator.orchestrate(execution.target, plan, context, execution.document_name)
    verification = result.verification_result
    if not result.success or verification is None or not verification.success:
        raise RuntimeError("public-key setup plan did not complete")
    return secret_ref


def install_instance_account_credentials(
    *,
    range_id: int,
    instance_key: str,
    platform: str,
    instance_output: dict[str, Any],
    accounts: Iterable[RaesPlanAccount],
    secret_ops: RaesAccountCredentialOps,
) -> dict[str, str]:
    """Install and verify every enabled authored-account credential on one guest.

    Returns the installed credential's Secret Manager reference keyed by compiled
    account address. The reference is *already* minted deterministically while
    installing; retaining it here (#1710) lets participant access be brokered
    through the account the scenario authored, rather than minting a parallel
    credential or exposing the reserved provisioner-management secret. A
    reference is returned only for a credential that installed **and** verified,
    so a published access binding always has a working credential behind it.
    """
    enabled_accounts = tuple(account for account in accounts if not account.disabled)
    if not enabled_accounts:
        return {}
    try:
        execution = secret_ops.execution_builder(instance_output, provider="gcp", os_type=platform, role="raes-node")
    except Exception:
        raise RaesAccountCredentialError("failed to establish authored-account credential setup channel") from None
    try:
        try:
            execution.wait_for_ready(timeout_seconds=300)
        except Exception:
            raise RaesAccountCredentialError("failed to establish authored-account credential setup channel") from None
        orchestrator = secret_ops.orchestrator_factory(execution.executor)
        secret_refs: dict[str, str] = {}
        for account in enabled_accounts:
            try:
                if account.auth_method == "password":
                    secret_ref = _run_password_strategy(
                        orchestrator, execution, range_id, instance_key, platform, account, secret_ops
                    )
                elif account.auth_method == "publickey":
                    secret_ref = _run_public_key_strategy(
                        orchestrator, execution, range_id, instance_key, platform, account, secret_ops
                    )
                # Defense in depth; the plan parser rejects this first.
                else:
                    raise ValueError("unsupported authored-account credential strategy")
            except Exception:
                raise RaesAccountCredentialError("failed to realize authored-account credential") from None
            secret_refs[account.address] = secret_ref
        return secret_refs
    finally:
        execution.close()


def delete_instance_account_credentials(
    range_id: int,
    instance_key: str,
    accounts: Iterable[RaesPlanAccount],
    secret_ops: RaesAccountCredentialOps,
) -> None:
    """Delete every deterministic credential secret reconstructible from the plan."""
    for account in accounts:
        secret_ops.delete(range_id, instance_key, account.username, account.auth_method)
