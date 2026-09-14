"""Truthful range cleanup-outcome projection (#2086, ADR-062-R4/R5).

Reports the distinct cleanup facts from durable state as one authorized
projection. Verified terminal cleanup is gated on durable, scoped provider
inventory/readback evidence (``RangeCleanupVerification``), never on a logical
lifecycle status: a range whose lifecycle reached ``DESTROYED`` but has no
inventory evidence is reported ``pending`` with an unconfirmed-inventory
obligation, not ``verified_terminal``. A failed / dead-lettered / interrupt-
exhausted operation is ``unknown`` (residual resources possible, absence not
proven), and an absent range is ``unknown`` -- never an empty success. A timeout,
DLQ, FAILED, or missing worker task is never treated as proof that resources are
gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

__all__ = [
    "CLEANUP_NOT_APPLICABLE",
    "CLEANUP_PENDING",
    "CLEANUP_UNKNOWN",
    "CLEANUP_VERIFIED_TERMINAL",
    "CleanupObligation",
    "RangeCleanupOutcome",
    "project_range_cleanup_outcome",
]

CLEANUP_NOT_APPLICABLE = "not_applicable"
CLEANUP_PENDING = "pending"
CLEANUP_UNKNOWN = "unknown"
CLEANUP_VERIFIED_TERMINAL = "verified_terminal"

# Range statuses that own live resources but owe no teardown yet.
_ACTIVE_STATUSES = frozenset({"pending", "provisioning", "ready", "pausing", "paused", "resuming"})


@dataclass(frozen=True)
class CleanupObligation:
    """One retained cleanup obligation derived from durable state."""

    code: str
    detail: str


@dataclass(frozen=True)
class RangeCleanupOutcome:
    """Distinct cleanup facts for a range operation."""

    request_id: str
    found: bool
    operation_status: str
    dispatch_status: str
    cancel_state: str
    cleanup: str
    residual_obligations: list[CleanupObligation] = field(default_factory=list)
    # Present only when scoped inventory/readback evidence exists.
    verification_observed_at: str | None = None
    verification_scope: dict[str, Any] | None = None


def _absent_outcome(rid: UUID) -> RangeCleanupOutcome:
    return RangeCleanupOutcome(
        request_id=str(rid),
        found=False,
        operation_status="absent",
        dispatch_status="none",
        cancel_state="none",
        cleanup=CLEANUP_UNKNOWN,
        residual_obligations=[
            CleanupObligation("range_absent", "no range record for request; cleanup cannot be confirmed")
        ],
    )


def _classify_with_verification(view, obligations: list[CleanupObligation]) -> str:
    """Classify cleanup from inventory/readback evidence and append residual obligations."""
    from engine.models import CleanupVerificationOutcome

    if view.outcome == CleanupVerificationOutcome.VERIFIED_ABSENT.value:
        return CLEANUP_VERIFIED_TERMINAL
    if view.outcome == CleanupVerificationOutcome.RESIDUALS_FOUND.value:
        for category in view.residual_categories:
            name = str(category.get("category", "resource")) if isinstance(category, dict) else "resource"
            count = category.get("count", "?") if isinstance(category, dict) else "?"
            obligations.append(CleanupObligation("residual_resource", f"{name}: {count} still present"))
        if not view.residual_categories:
            obligations.append(CleanupObligation("residual_resource", "provider inventory found residual resources"))
        return CLEANUP_UNKNOWN
    obligations.append(
        CleanupObligation("inventory_incomplete", "provider inventory could not complete; absence not proven")
    )
    return CLEANUP_UNKNOWN


def _classify_without_verification(status: str, obligations: list[CleanupObligation]) -> str:
    """Classify cleanup from lifecycle status when no inventory evidence exists yet."""
    from shared.enums import ResourceStatus

    if status == ResourceStatus.DESTROYING.value:
        obligations.append(
            CleanupObligation("teardown_in_progress", "destroy dispatched; provider inventory not yet observed")
        )
        return CLEANUP_PENDING
    if status == ResourceStatus.DESTROYED.value:
        # Lifecycle terminal, but ADR-062-R4 forbids claiming verified without readback.
        obligations.append(
            CleanupObligation(
                "provider_inventory_unconfirmed",
                "lifecycle terminal but no provider inventory/readback evidence yet",
            )
        )
        return CLEANUP_PENDING
    if status == ResourceStatus.FAILED.value:
        obligations.append(
            CleanupObligation("operation_failed", "operation failed; residual resources possible, absence not proven")
        )
        return CLEANUP_UNKNOWN
    obligations.append(
        CleanupObligation("provider_inventory_unconfirmed", "cleanup not verified by provider inventory/readback")
    )
    return CLEANUP_UNKNOWN


def project_range_cleanup_outcome(request_id: str | UUID) -> RangeCleanupOutcome:
    """Project the truthful cleanup outcome for a request's range from durable state."""
    from engine.models import ProvisionerLaunchIntent, Range

    from ._cleanup_verification import latest_cleanup_verification

    rid = UUID(str(request_id))
    row = Range.objects.filter(request__request_id=rid).only("status", "provisioner_operation_id").first()
    if row is None:
        return _absent_outcome(rid)

    intent = (
        ProvisionerLaunchIntent.objects.filter(operation_id=row.provisioner_operation_id).first()
        if row.provisioner_operation_id
        else None
    )
    dispatch_status = str(intent.status) if intent else "none"
    cancel_state = str(intent.interrupt_state or "none") if intent else "none"

    obligations: list[CleanupObligation] = []
    status = str(row.status)
    verification = latest_cleanup_verification(rid)
    verification_observed_at = verification.observed_at.isoformat() if verification else None
    verification_scope = verification.scope if verification else None

    if status in _ACTIVE_STATUSES:
        cleanup = CLEANUP_NOT_APPLICABLE
    elif verification is not None:
        cleanup = _classify_with_verification(verification, obligations)
    else:
        cleanup = _classify_without_verification(status, obligations)

    if cleanup not in (CLEANUP_NOT_APPLICABLE, CLEANUP_VERIFIED_TERMINAL) and intent is not None:
        if str(intent.status) == "DLQ":
            obligations.append(
                CleanupObligation("dispatch_dead_lettered", "launch dispatch exhausted; outcome indeterminate")
            )
        if str(intent.interrupt_state) == "EXHAUSTED":
            obligations.append(
                CleanupObligation(
                    "interrupt_exhausted", "cancellation deadline elapsed without confirmed terminal absence"
                )
            )

    return RangeCleanupOutcome(
        request_id=str(rid),
        found=True,
        operation_status=status,
        dispatch_status=dispatch_status,
        cancel_state=cancel_state,
        cleanup=cleanup,
        residual_obligations=obligations,
        verification_observed_at=verification_observed_at,
        verification_scope=verification_scope,
    )
