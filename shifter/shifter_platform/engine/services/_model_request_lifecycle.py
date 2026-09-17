"""Dispatch leases, settlement, revocation fencing and reconciliation (M04).

These operations move a reserved request through its one-shot dispatch, settle
verified usage exactly once against the immutable price snapshot, retain a
conservative hold when the outcome is ambiguous, and fence revocation without
transferring or erasing existing liabilities. Reconciliation may move an unknown
hold from reserved to conservatively spent at the same value, but never refunds
it on a timer, invokes a provider, or replays a request.

Canonical lock order matches ``_model_request_accounting``: allocation/grant,
sharing authority, account/window rows, then the request row; the strict
body-free audit is committed last.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction

from shared.model_access import BillingBound, ContractError, validate_catalog
from shared.model_access.effective_policy import EffectivePolicy
from shared.model_access.provider import ProviderUsage

from ._model_request_accounting import _checked_ceil, _recheck_authority

if TYPE_CHECKING:
    from engine.models import ModelBudgetAccount, ModelRequestReservation

# Default provider-completion horizon: how long an ambiguous dispatch retains its
# concurrency slot and conservative hold before reconciliation may charge it.
_DEFAULT_HORIZON_SECONDS = 900
_DISPATCH_LEASE_SECONDS = 10
_CONTINUATION_LEASE_SECONDS = 5


@dataclass(frozen=True)
class DispatchGrant:
    """The single opaque dispatch token and its short expiry."""

    dispatch_token: str
    dispatch_deadline: datetime


def open_dispatch(*, request_uuid: UUID, now: datetime | None = None) -> DispatchGrant:
    """Acquire the one dispatch attempt and its short lease before transport."""
    from engine.models import ModelAllocation, ModelDispatchLease, ModelPendingGrant, ModelRequestReservation

    moment = now or datetime.now(UTC)
    with transaction.atomic():
        # Owner-first lock order: allocation, grant and authority before the request
        # row, matching reserve and revocation so dispatch never deadlocks against them.
        reservation = ModelRequestReservation.objects.get(request_uuid=request_uuid)
        allocation = ModelAllocation.objects.select_for_update().get(pk=reservation.allocation_id)
        _assert_allocation_current(allocation, moment)
        _assert_request_current(reservation, moment)
        _assert_grant_current(ModelPendingGrant, reservation)
        _recheck_authority(allocation)
        reservation = ModelRequestReservation.objects.select_for_update().get(pk=reservation.pk)
        if reservation.state != "reserved":
            raise ContractError("request.dispatch_state")
        token = secrets.token_urlsafe(24)
        lease = ModelDispatchLease.objects.create(
            reservation=reservation,
            dispatch_token=token,
            dispatch_deadline=moment + timedelta(seconds=_DISPATCH_LEASE_SECONDS),
            transport_status="dispatching",
        )
        reservation.state = "dispatched"
        reservation.save(update_fields=["state", "updated_at"])
        return DispatchGrant(dispatch_token=token, dispatch_deadline=lease.dispatch_deadline)


def check_dispatch_lease(*, request_uuid: UUID, dispatch_token: str, now: datetime | None = None) -> bool:
    """Validate the dispatch fence immediately before transport; raise if not live.

    The broker calls this right before the provider call. It fails closed on an
    unknown lease, a token mismatch, an expired deadline, a revoked or closed
    transport fence, a stale grant epoch, or an advanced authority revision.
    """
    from engine.models import ModelAllocation, ModelDispatchLease, ModelPendingGrant, ModelRequestReservation

    moment = now or datetime.now(UTC)
    with transaction.atomic():
        reservation = ModelRequestReservation.objects.get(request_uuid=request_uuid)
        allocation = ModelAllocation.objects.select_for_update().get(pk=reservation.allocation_id)
        _assert_allocation_current(allocation, moment)
        _assert_request_current(reservation, moment)
        _assert_grant_current(ModelPendingGrant, reservation)
        _recheck_authority(allocation)
        lease = ModelDispatchLease.objects.select_for_update().filter(reservation_id=reservation.pk).first()
        if lease is None:
            raise ContractError("request.no_lease")
        if lease.transport_status in {"revoking", "revoked", "closed"}:
            raise ContractError("request.revoked")
        if not secrets.compare_digest(lease.dispatch_token, dispatch_token):
            raise ContractError("request.lease_token_mismatch")
        if lease.dispatch_deadline <= moment:
            raise ContractError("request.lease_expired")
        return True


def renew_continuation_lease(*, request_uuid: UUID, now: datetime | None = None) -> int:
    """Advance the continuation fence only after rechecking current authority."""
    from engine.models import ModelAllocation, ModelDispatchLease, ModelPendingGrant, ModelRequestReservation

    moment = now or datetime.now(UTC)
    with transaction.atomic():
        # Owner-first lock order: allocation, grant and authority before the request row.
        reservation = ModelRequestReservation.objects.get(request_uuid=request_uuid)
        allocation = ModelAllocation.objects.select_for_update().get(pk=reservation.allocation_id)
        _assert_allocation_current(allocation, moment)
        _assert_request_current(reservation, moment)
        _assert_grant_current(ModelPendingGrant, reservation)
        _recheck_authority(allocation)
        reservation = ModelRequestReservation.objects.select_for_update().get(pk=reservation.pk)
        lease = ModelDispatchLease.objects.select_for_update().get(reservation=reservation)
        if lease.transport_status in {"revoking", "revoked", "closed"}:
            raise ContractError("request.revoked")
        effective_deadline = lease.continuation_deadline or lease.dispatch_deadline
        if effective_deadline <= moment:
            # A stale worker cannot renew after its own lease already expired.
            raise ContractError("request.lease_expired")
        lease.continuation_revision += 1
        lease.continuation_deadline = moment + timedelta(seconds=_CONTINUATION_LEASE_SECONDS)
        lease.transport_status = "active"
        lease.save(update_fields=["continuation_revision", "continuation_deadline", "transport_status"])
        return lease.continuation_revision


def settle_request(
    *,
    request_uuid: UUID,
    usage: ProviderUsage,
    provider_request_ref: str | None = None,
    now: datetime | None = None,
) -> int:
    """Settle proven usage exactly once against the immutable price snapshot."""
    from engine.models import ModelRequestReservation

    if _usage_incomplete(usage):
        # Incomplete or unverified evidence never finalizes a request as zero/partial
        # cost. Retain the conservative hold and route it to reconciliation instead.
        pending = ModelRequestReservation.objects.filter(request_uuid=request_uuid).first()
        if pending is not None and pending.settlement_state != "settled":
            charge_unknown(
                request_uuid=request_uuid,
                uncertainty_reason="incomplete_usage",
                provider_request_ref=provider_request_ref,
                now=now,
            )
        raise ContractError("request.usage_incomplete")
    with transaction.atomic():
        reservation = _lock_request(request_uuid=request_uuid)
        if reservation.settlement_state == "settled":
            return _settled_total(reservation)  # idempotent
        if reservation.settlement_state == "unknown_charged":
            return apply_late_evidence(request_uuid=request_uuid, usage=usage, now=now)
        settled = _settled_charge(reservation, usage)
        _apply_settlement(reservation, settled, usage, provider_request_ref)
        _close_lease(reservation)
        _release_liability(reservation)
        _lifecycle_audit(reservation, "MODEL_REQUEST_SETTLE", f"settle charge={settled}")
        return settled


def release_before_dispatch(*, request_uuid: UUID, now: datetime | None = None) -> None:
    """Release money/concurrency on a proven pre-transport failure; keep rate count."""

    with transaction.atomic():
        reservation = _lock_request(request_uuid=request_uuid)
        if reservation.state != "reserved":
            raise ContractError("request.dispatch_state")
        for posting, account in _locked_postings(reservation):
            _release_posting(account, posting)
        reservation.state = "settled"
        reservation.settlement_state = "settled"
        reservation.save(update_fields=["state", "settlement_state", "updated_at"])
        _release_liability(reservation)
        _lifecycle_audit(reservation, "MODEL_REQUEST_SETTLE", "released pre-dispatch")


def charge_unknown(
    *,
    request_uuid: UUID,
    uncertainty_reason: str,
    provider_request_ref: str | None = None,
    now: datetime | None = None,
    horizon_seconds: int = _DEFAULT_HORIZON_SECONDS,
) -> None:
    """Retain the conservative hold for an ambiguous dispatch and record an obligation."""
    from engine.models import ModelReconciliationObligation

    moment = now or datetime.now(UTC)
    with transaction.atomic():
        reservation = _lock_request(request_uuid=request_uuid)
        if reservation.settlement_state == "settled":
            return  # already resolved; never reopen a settled request
        reservation.state = "unknown"
        reservation.uncertainty_reason = uncertainty_reason
        reservation.provider_request_ref = provider_request_ref or ""
        reservation.save(update_fields=["state", "uncertainty_reason", "provider_request_ref", "updated_at"])
        ModelReconciliationObligation.objects.get_or_create(
            reservation=reservation,
            defaults={
                "allocation": reservation.allocation,
                "provider_request_ref": provider_request_ref or "",
                "next_attempt_at": moment + timedelta(seconds=horizon_seconds),
                "outcome": "unknown",
            },
        )
        _mark_lease_closing(reservation)
        _lifecycle_audit(reservation, "MODEL_REQUEST_UNKNOWN", f"unknown reason={uncertainty_reason}")


def fence_revoked_requests(*, allocation_id: UUID, now: datetime | None = None) -> int:
    """Mark active dispatch leases revoking after a grant epoch is fenced."""
    from engine.models import ModelDispatchLease, ModelRequestReservation

    with transaction.atomic():
        fenced = 0
        reservations = (
            ModelRequestReservation.objects.select_for_update()
            .filter(allocation_id=allocation_id, state__in=["reserved", "dispatched"])
            .order_by("pk")
        )
        for reservation in reservations:
            lease = ModelDispatchLease.objects.select_for_update().filter(reservation=reservation).first()
            if lease is not None and lease.transport_status in {"dispatching", "active"}:
                lease.transport_status = "revoking"
                lease.save(update_fields=["transport_status"])
                fenced += 1
        return fenced


def close_expired_revocations(*, now: datetime | None = None, limit: int = 100) -> int:
    """Transition a revoking lease to revoked once its residual authority expires."""
    from engine.models import ModelDispatchLease

    moment = now or datetime.now(UTC)
    closed = 0
    lease_ids = list(
        ModelDispatchLease.objects.filter(transport_status="revoking")
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for lease_id in lease_ids:
        with transaction.atomic():
            lease = ModelDispatchLease.objects.select_for_update().get(pk=lease_id)
            if lease.transport_status != "revoking":
                continue
            expired = lease.dispatch_deadline <= moment and (
                lease.continuation_deadline is None or lease.continuation_deadline <= moment
            )
            if expired or lease.acknowledged:
                lease.transport_status = "revoked"
                lease.save(update_fields=["transport_status"])
                closed += 1
    return closed


def reconcile_model_requests(*, now: datetime | None = None, limit: int = 100) -> int:
    """Move stale unknown holds from reserved to conservatively spent; never refund."""
    from engine.models import ModelReconciliationObligation

    moment = now or datetime.now(UTC)
    processed = 0
    obligation_ids = list(
        ModelReconciliationObligation.objects.filter(outcome="unknown", next_attempt_at__lte=moment)
        .order_by("next_attempt_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for obligation_id in obligation_ids:
        with transaction.atomic():
            candidate = ModelReconciliationObligation.objects.get(pk=obligation_id)
            reservation = _lock_reservation(candidate.reservation_id)
            obligation = ModelReconciliationObligation.objects.select_for_update().get(pk=obligation_id)
            if obligation.outcome != "unknown":
                continue
            if reservation.settlement_state == "open":
                _charge_hold(reservation)
                reservation.settlement_state = "unknown_charged"
                reservation.save(update_fields=["settlement_state", "updated_at"])
                _release_liability(reservation)
                _lifecycle_audit(reservation, "MODEL_REQUEST_UNKNOWN", "reconciled unknown to spent")
            obligation.outcome = "charged"
            obligation.attempts += 1
            obligation.save(update_fields=["outcome", "attempts", "updated_at"])
            processed += 1
    return processed


def reconcile_expired_dispatches(*, now: datetime | None = None, limit: int = 100) -> int:
    """Turn a crashed/stale dispatched request into a durable unknown obligation.

    A worker that dies after open_dispatch leaves a dispatched reservation holding
    budget, a concurrency slot and an unresolved liability. Once its lease deadline
    passes this sweep retains the conservative hold and records the unknown
    obligation so reconcile_model_requests can conservatively charge it - it is
    never released for free and never replayed.
    """
    from engine.models import ModelDispatchLease, ModelReconciliationObligation, ModelRequestReservation

    moment = now or datetime.now(UTC)
    swept = 0
    reservation_ids = list(
        ModelRequestReservation.objects.filter(state__in=["reserved", "dispatched"], settlement_state="open")
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for reservation_id in reservation_ids:
        with transaction.atomic():
            reservation = _lock_reservation(reservation_id)
            if reservation.state == "reserved":
                # No provider transport is permitted before open_dispatch. An
                # abandoned admission can therefore release its money safely.
                if reservation.created_at + timedelta(seconds=120) <= moment:
                    release_before_dispatch(request_uuid=reservation.request_uuid, now=moment)
                    swept += 1
                continue
            if reservation.state != "dispatched" or reservation.settlement_state != "open":
                continue
            lease = ModelDispatchLease.objects.select_for_update().filter(reservation=reservation).first()
            if lease is None:
                continue
            effective_deadline = lease.continuation_deadline or lease.dispatch_deadline
            if effective_deadline > moment:
                continue  # still within a live lease
            reservation.state = "unknown"
            reservation.uncertainty_reason = "dispatch_lease_expired"
            reservation.save(update_fields=["state", "uncertainty_reason", "updated_at"])
            ModelReconciliationObligation.objects.get_or_create(
                reservation=reservation,
                defaults={
                    "allocation": reservation.allocation,
                    "provider_request_ref": reservation.provider_request_ref or "",
                    "next_attempt_at": moment + timedelta(seconds=_DEFAULT_HORIZON_SECONDS),
                    "outcome": "unknown",
                },
            )
            if lease.transport_status in {"dispatching", "active"}:
                lease.transport_status = "revoking"
                lease.save(update_fields=["transport_status"])
            _lifecycle_audit(reservation, "MODEL_REQUEST_UNKNOWN", "dispatch lease expired")
            swept += 1
    return swept


def apply_late_evidence(
    *,
    request_uuid: UUID,
    usage: ProviderUsage,
    now: datetime | None = None,
) -> int:
    """Adjust a conservatively-charged unknown once from later authoritative usage."""
    from engine.models import ModelReconciliationObligation

    with transaction.atomic():
        reservation = _lock_request(request_uuid=request_uuid)
        if reservation.settlement_state != "unknown_charged":
            raise ContractError("request.not_reconcilable")
        settled = _settled_charge(reservation, usage)
        _readjust_spend(reservation, settled)
        reservation.state = "settled"
        reservation.settlement_state = "settled"
        reservation.usage = _usage_payload(usage)
        reservation.save(update_fields=["state", "settlement_state", "usage", "updated_at"])
        obligation = ModelReconciliationObligation.objects.select_for_update().filter(reservation=reservation).first()
        if obligation is not None:
            obligation.outcome = "adjusted"
            obligation.last_observation = _usage_payload(usage)
            obligation.save(update_fields=["outcome", "last_observation", "updated_at"])
        _lifecycle_audit(reservation, "MODEL_REQUEST_ADJUST", f"late evidence charge={settled}")
        return settled


# --- shared helpers -------------------------------------------------------


def _assert_grant_current(grant_model, reservation: ModelRequestReservation) -> None:
    """Fail closed when the grant is revoked or its epoch has advanced."""
    grant = grant_model.objects.select_for_update().get(allocation_id=reservation.allocation_id)
    if grant.state != "pending" or grant.grant_epoch != reservation.grant_epoch:
        raise ContractError("request.revoked")


def _snapshot_catalog(reservation: ModelRequestReservation) -> object:
    """Reconstruct the immutable catalog captured on the allocation snapshot."""
    return validate_catalog(reservation.allocation.snapshot["catalog"])


def _usage_incomplete(usage: ProviderUsage) -> bool:
    """Evidence is incomplete unless every reported component is provider-verified."""
    return not usage.items or any(not item.provider_verified for item in usage.items)


def _settled_charge(reservation: ModelRequestReservation, usage: ProviderUsage) -> int:
    """Compute the settled charge from proven usage and the snapshot price schedule."""
    if _usage_incomplete(usage):
        raise ContractError("request.usage_incomplete")
    bound = BillingBound.model_validate(reservation.billing_bound)
    expected = {amount.component for amount in bound.amounts}
    if {item.component for item in usage.items} != expected:
        raise ContractError("request.usage_incomplete")
    catalog = _snapshot_catalog(reservation)
    alias = next((item for item in catalog.aliases if item.logical_alias == reservation.logical_alias), None)
    if alias is None:
        raise ContractError("request.alias_unavailable")
    schedule = next(
        (item for item in catalog.price_schedules if item.price_schedule_id == alias.price_schedule_id), None
    )
    if schedule is None:
        raise ContractError("request.price_unavailable")
    prices = {price.component: price for price in schedule.prices}
    total = 0
    for item in usage.items:
        if not item.provider_verified:
            continue  # unverified usage is never settled; it stays an unknown hold
        price = prices.get(item.component)
        if price is None:
            raise ContractError("request.price_unavailable")
        total += _checked_ceil(item.units, price.price_micro_units, price.unit_denominator)
    if total > reservation.canonical_request_cost:
        raise ContractError("request.usage_exceeds_bound")
    return total


def _locked_postings(reservation: ModelRequestReservation):
    """Yield each posting with its account locked in canonical order."""
    from engine.models import ModelBudgetAccount, ModelBudgetPosting

    postings = ModelBudgetPosting.objects.filter(reservation=reservation).order_by("account__account_ref")
    for posting in postings:
        account = ModelBudgetAccount.objects.select_for_update().get(pk=posting.account_id)
        yield posting, account


def _apply_settlement(reservation, settled, usage, provider_request_ref) -> None:
    """Charge each spend account the single request cost; free the concurrency slot."""
    for posting, account in _locked_postings(reservation):
        if account.dimension == "spend":
            account.spent += settled
            account.reserved = max(account.reserved - posting.held, 0)
            account.save(update_fields=["spent", "reserved"])
            posting.settled = settled
        elif account.dimension == "concurrency":
            account.active_leases = max(account.active_leases - posting.held, 0)
            account.save(update_fields=["active_leases"])
            posting.settled = 0
        else:  # rate: the request happened, so its window count stands
            posting.settled = posting.held
        posting.state = "settled"
        posting.save(update_fields=["settled", "state"])
    reservation.state = "settled"
    reservation.settlement_state = "settled"
    reservation.usage = _usage_payload(usage)
    reservation.provider_request_ref = provider_request_ref or ""
    reservation.save(update_fields=["state", "settlement_state", "usage", "provider_request_ref", "updated_at"])


def _charge_hold(reservation: ModelRequestReservation) -> None:
    """Move each spend hold to spent at the same value; free the concurrency slot."""
    for posting, account in _locked_postings(reservation):
        if account.dimension == "spend":
            account.spent += posting.held
            account.reserved = max(account.reserved - posting.held, 0)
            account.save(update_fields=["spent", "reserved"])
            posting.settled = posting.held
        elif account.dimension == "concurrency":
            account.active_leases = max(account.active_leases - posting.held, 0)
            account.save(update_fields=["active_leases"])
            posting.settled = 0
        else:
            posting.settled = posting.held
        posting.state = "settled"
        posting.save(update_fields=["settled", "state"])


def _readjust_spend(reservation: ModelRequestReservation, settled: int) -> None:
    """Adjust an already-charged spend account to the proven value (append-only)."""
    for posting, account in _locked_postings(reservation):
        if account.dimension != "spend":
            continue
        account.spent = max(account.spent - posting.settled + settled, 0)
        account.save(update_fields=["spent"])
        posting.settled = settled
        posting.save(update_fields=["settled"])


def _release_posting(account: ModelBudgetAccount, posting) -> None:
    """Release a money or concurrency hold; a rate count is retained for abuse control."""
    if account.dimension == "spend":
        account.reserved = max(account.reserved - posting.held, 0)
        account.save(update_fields=["reserved"])
        posting.settled = 0
        posting.state = "released"
    elif account.dimension == "concurrency":
        account.active_leases = max(account.active_leases - posting.held, 0)
        account.save(update_fields=["active_leases"])
        posting.settled = 0
        posting.state = "released"
    else:  # rate accounting is retained on a pre-dispatch failure
        posting.settled = posting.held
        posting.state = "settled"
    posting.save(update_fields=["settled", "state"])


def _settled_total(reservation: ModelRequestReservation) -> int:
    """Sum the settled spend already recorded for an idempotent settle."""
    from engine.models import ModelBudgetPosting

    postings = ModelBudgetPosting.objects.filter(reservation=reservation, account__dimension="spend")
    return max((posting.settled for posting in postings), default=0)


def _lock_reservation(reservation_id: int) -> ModelRequestReservation:
    """Lock a reservation row by its integer identity."""
    from engine.models import ModelRequestReservation

    candidate = ModelRequestReservation.objects.get(pk=reservation_id)
    return _lock_request(request_uuid=candidate.request_uuid)


def _assert_request_current(reservation, moment: datetime) -> None:
    policy = EffectivePolicy.model_validate(reservation.allocation.snapshot["effective_policy"])
    if policy.effective_profile is None:
        raise ContractError("request.policy_unavailable")
    seconds = min(120, policy.effective_profile.limits.max_request_seconds)
    if reservation.created_at + timedelta(seconds=seconds) <= moment:
        raise ContractError("request.deadline_expired")


def _assert_allocation_current(allocation, moment: datetime) -> None:
    """A lease cannot extend the allocation's original hard deadline."""
    if allocation.released_at is not None or allocation.deadline <= moment:
        raise ContractError("request.allocation_expired")


def _lock_request(*, request_uuid: UUID) -> ModelRequestReservation:
    """Serialize settlement against admission in the same owner-first order."""
    from engine.models import ModelAllocation, ModelPendingGrant, ModelRequestReservation

    candidate = ModelRequestReservation.objects.get(request_uuid=request_uuid)
    ModelAllocation.objects.select_for_update().get(pk=candidate.allocation_id)
    ModelPendingGrant.objects.select_for_update().get(allocation_id=candidate.allocation_id)
    # Account rows precede request rows, matching the admission transaction.
    list(_locked_postings(candidate))
    return ModelRequestReservation.objects.select_for_update().get(pk=candidate.pk)


def _close_lease(reservation: ModelRequestReservation) -> None:
    """Close a dispatch lease on normal settlement."""
    from engine.models import ModelDispatchLease

    lease = ModelDispatchLease.objects.select_for_update().filter(reservation=reservation).first()
    if lease is not None and lease.transport_status not in {"revoked", "closed"}:
        lease.transport_status = "closed"
        lease.save(update_fields=["transport_status"])


def _mark_lease_closing(reservation: ModelRequestReservation) -> None:
    """A dispatched request whose outcome is unknown keeps its lease until it expires."""
    from engine.models import ModelDispatchLease

    lease = ModelDispatchLease.objects.select_for_update().filter(reservation=reservation).first()
    if lease is not None and lease.transport_status in {"dispatching", "active"}:
        lease.transport_status = "revoking"
        lease.save(update_fields=["transport_status"])


def _release_liability(reservation: ModelRequestReservation) -> None:
    """Drop the allocation's unresolved-liability fence for a resolved request."""
    from engine.models import ModelAllocation

    ModelAllocation.objects.filter(pk=reservation.allocation_id, unresolved_liabilities__gt=0).update(
        unresolved_liabilities=_decrement()
    )


def _decrement():
    """Return an F-expression that decrements the liability fence by one."""
    from django.db.models import F

    return F("unresolved_liabilities") - 1


def _usage_payload(usage: ProviderUsage) -> dict:
    """Render a body-free usage summary safe for persistence."""
    return {"items": [{"component": item.component.value, "units": item.units} for item in usage.items]}


def _lifecycle_audit(reservation: ModelRequestReservation, action_name: str, context: str) -> None:
    """Commit a strict body-free lifecycle audit event."""
    from shared.audit import AuditActorType, AuditEvent, audit_log
    from shared.audit.vocabulary import AuditAction, AuditEntityType

    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.MODEL_REQUEST.value,
            entity_id=reservation.pk,
            action=getattr(AuditAction, action_name).value,
            actor_type=AuditActorType.SYSTEM,
            context=context,
            entity_ref=str(reservation.request_uuid),
        ),
        strict=True,
    )
