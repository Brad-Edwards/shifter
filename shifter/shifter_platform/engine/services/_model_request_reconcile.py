"""Bounded request-accounting reconciliation on the incumbent worker (M04).

At each obligation's deadline this pass moves a stale unknown hold from reserved to
conservatively spent at the same value, sweeps a crashed/stale dispatched request
into a durable unknown obligation, and closes expired revocation fences. It never
refunds a hold on a timer, invokes a provider, or replays a request. Later
authoritative provider evidence adjusts a conservatively-charged request once as an
append-only correction.

The shared posting/audit/liability helpers live in ``_model_request_lifecycle`` and
are reused here; this module adds only the reconciliation-specific mutators.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction

from shared.model_access import ContractError
from shared.model_access.provider import ProviderUsage

from ._model_request_lifecycle import (
    _lifecycle_audit,
    _locked_postings,
    _release_liability,
    _settled_charge,
    _usage_incomplete,
    _usage_payload,
)

if TYPE_CHECKING:
    from engine.models import ModelRequestReservation


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
            obligation = ModelReconciliationObligation.objects.select_for_update().get(pk=obligation_id)
            if obligation.outcome != "unknown":
                continue
            reservation = _lock_reservation(obligation.reservation_id)
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
        ModelRequestReservation.objects.filter(state="dispatched", settlement_state="open")
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for reservation_id in reservation_ids:
        with transaction.atomic():
            reservation = ModelRequestReservation.objects.select_for_update().get(pk=reservation_id)
            if reservation.state != "dispatched" or reservation.settlement_state != "open":
                continue
            lease = ModelDispatchLease.objects.select_for_update().filter(reservation=reservation).first()
            if lease is None:
                continue
            effective_deadline = lease.continuation_deadline or lease.dispatch_deadline
            if effective_deadline > moment:
                # Still within a live lease.
                continue
            reservation.state = "unknown"
            reservation.uncertainty_reason = "dispatch_lease_expired"
            reservation.save(update_fields=["state", "uncertainty_reason", "updated_at"])
            ModelReconciliationObligation.objects.get_or_create(
                reservation=reservation,
                defaults={
                    "allocation": reservation.allocation,
                    "provider_request_ref": reservation.provider_request_ref or "",
                    "next_attempt_at": moment,
                    "outcome": "unknown",
                },
            )
            if lease.transport_status in {"dispatching", "active"}:
                lease.transport_status = "revoking"
                lease.save(update_fields=["transport_status"])
            _lifecycle_audit(reservation, "MODEL_REQUEST_UNKNOWN", "dispatch lease expired")
            swept += 1
    return swept


def apply_late_evidence(*, request_uuid: UUID, usage: ProviderUsage) -> int:
    """Adjust a conservatively-charged unknown once from later authoritative usage."""
    from engine.models import ModelReconciliationObligation, ModelRequestReservation

    with transaction.atomic():
        reservation = ModelRequestReservation.objects.select_for_update().get(request_uuid=request_uuid)
        if reservation.settlement_state != "unknown_charged":
            raise ContractError("request.not_reconcilable")
        if _usage_incomplete(reservation, usage):
            # Incomplete late evidence must never erase the conservative charge; the
            # request stays reconcilable until complete authoritative usage arrives.
            raise ContractError("request.usage_incomplete")
        settled = _settled_charge(reservation, usage)
        _readjust_spend(reservation, settled)
        reservation.settlement_state = "settled"
        reservation.usage = _usage_payload(usage)
        reservation.save(update_fields=["settlement_state", "usage", "updated_at"])
        obligation = ModelReconciliationObligation.objects.select_for_update().filter(reservation=reservation).first()
        if obligation is not None:
            obligation.outcome = "adjusted"
            obligation.last_observation = _usage_payload(usage)
            obligation.save(update_fields=["outcome", "last_observation", "updated_at"])
        _lifecycle_audit(reservation, "MODEL_REQUEST_ADJUST", f"late evidence charge={settled}")
        return settled


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


def _lock_reservation(reservation_id: int) -> ModelRequestReservation:
    """Lock a reservation row by its integer identity."""
    from engine.models import ModelRequestReservation

    return ModelRequestReservation.objects.select_for_update().get(pk=reservation_id)
