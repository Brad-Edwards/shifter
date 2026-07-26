"""Capacity-aware admission: assess declared demand against observed headroom (PLAT-201).

The Engine owns the authoritative decision. CTF declares intent, CMS keeps
authenticated creation and backend admission, and the provisioner realizes an
already-admitted request -- it never re-decides.

Ordering matters here and is the reason this module is not one transaction:
provider state cannot be locked, so limits and usage are read *outside* any
database transaction, and the transaction that follows re-reads committed
overlapping reservations before writing its own. Freshness limits and per-metric
safety margins carry the residual race, which is why an unreadable or stale
measurement is ``INDETERMINATE`` rather than a fabricated zero.

A rejected assessment reserves nothing: capacity a launch will never use must
not be withheld from the next event.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import transaction

from shared.capacity import (
    CapacityAssessmentResult,
    CapacityOutcome,
    CapacityReasonCode,
    EnforcementMode,
    MetricVerdict,
    PartitionRef,
    evaluate_metric,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shared.capacity import CapacityMetricSpec, ObservationResult
    from shared.capacity.catalog import CapacityCatalog

logger = logging.getLogger(__name__)

#: Window assumed when a declaration carries no explicit consumption window.
_DEFAULT_WINDOW_HOURS = 8

_UNKNOWN_PARTITION = PartitionRef(
    name="",
    provider="",
    account="",
    region="",
    backend="",
)


def assess_event_capacity(
    *,
    event_ref: UUID,
    partition_name: str,
    demand: Mapping[str, float],
    window_start: datetime | None,
    window_end: datetime | None,
    catalog: CapacityCatalog,
    inventory: Any,
    now: datetime,
) -> CapacityAssessmentResult:
    """Assess ``demand`` for ``event_ref`` against ``partition_name`` and reserve.

    Returns the folded result. Persists an immutable assessment row always, and
    open reservations only when the result does not block. Never raises for a
    provider or configuration problem: those degrade to ``INDETERMINATE`` so a
    capacity read cannot break the pre-spinup path.
    """
    partition = catalog.partitions.get(partition_name)
    if partition is None:
        # An undeclared partition is a deployment gap, not free headroom.
        logger.warning("capacity: no declared partition '%s' for event %s", partition_name, event_ref)
        return _record_unassessable(
            event_ref=event_ref,
            partition_name=partition_name,
            policy_version=catalog.policy_version,
            demand=demand,
            now=now,
            reason_code=CapacityReasonCode.METRIC_UNSUPPORTED,
        )

    start, end = _resolve_window(window_start, window_end, now)
    specs = {spec.name: spec for spec in catalog.metrics_for(partition_name)}

    # Phase 1 -- observe. Strictly outside any transaction.
    observations = {
        metric_name: _observe(inventory, specs.get(metric_name), partition)
        for metric_name in demand
        if metric_name in specs
    }

    # Phase 2 -- decide and reserve, re-reading committed reservations under lock.
    with transaction.atomic():
        verdicts = tuple(
            _verdict_for(
                spec=specs[metric_name],
                demanded=demanded,
                observed=observations.get(metric_name),
                reserved=_committed_reservation(partition_name, metric_name, start, end),
                now=now,
            )
            for metric_name, demanded in sorted(demand.items())
            if metric_name in specs
        )
        verdicts += tuple(
            _unsupported_verdict(metric_name, now) for metric_name in sorted(demand) if metric_name not in specs
        )

        result = CapacityAssessmentResult(
            partition=partition,
            policy_version=catalog.policy_version,
            observed_at=now,
            verdicts=verdicts,
        )
        assessment = _persist_assessment(event_ref, result)
        if not result.blocking:
            _persist_reservations(assessment, event_ref, partition_name, demand, specs, start, end)

    _audit_assessment(assessment, result)
    return result


def release_capacity_reservations(
    event_ref: UUID,
    *,
    now: datetime | None = None,
    request_id: UUID | None = None,
) -> int:
    """Release an event's open reservations, returning how many were released.

    Idempotent by construction: only rows with a null ``released_at`` are
    touched, so cancellation, terminal cleanup, and a retried reconciliation can
    all call this without double-counting. Passing ``request_id`` narrows the
    release to one provisioning request.
    """
    from django.utils import timezone

    from engine.models import CapacityReservation

    queryset = CapacityReservation.objects.filter(event_ref=event_ref, released_at__isnull=True)
    if request_id is not None:
        queryset = queryset.filter(request_id=request_id)
    return queryset.update(released_at=now or timezone.now())


def _observe(inventory: Any, spec: CapacityMetricSpec | None, partition: PartitionRef) -> ObservationResult | None:
    """Read one metric, converting any adapter failure into an unmeasured result."""
    if spec is None:
        return None
    try:
        return inventory.observe(spec, partition)
    except Exception:
        # Adapters are contractually non-raising; this is the belt-and-braces
        # path so a misbehaving adapter cannot break provisioning.
        logger.warning("capacity: inventory raised for metric %s in %s", spec.name, partition.name)
        return None


def _verdict_for(
    *,
    spec: CapacityMetricSpec,
    demanded: float,
    observed: ObservationResult | None,
    reserved: float,
    now: datetime,
) -> MetricVerdict:
    """Fold one metric's observation and committed reservations into a verdict."""
    if observed is None:
        return MetricVerdict(
            metric_name=spec.name,
            outcome=CapacityOutcome.INDETERMINATE,
            reason_code=CapacityReasonCode.MEASUREMENT_UNAVAILABLE,
            enforcement=spec.enforcement,
            observed_at=None,
        )
    if observed.observation is None:
        return MetricVerdict(
            metric_name=spec.name,
            outcome=CapacityOutcome.INDETERMINATE,
            reason_code=observed.reason_code or CapacityReasonCode.MEASUREMENT_UNAVAILABLE,
            enforcement=spec.enforcement,
            observed_at=None,
        )
    # Overlapping commitments are part of what is already spoken for.
    observation = replace(observed.observation, reserved=observed.observation.reserved + reserved)
    return evaluate_metric(spec, demand=demanded, observation=observation, now=now)


def _unsupported_verdict(metric_name: str, now: datetime) -> MetricVerdict:
    """Verdict for demand naming a metric the catalog does not declare."""
    return MetricVerdict(
        metric_name=metric_name,
        outcome=CapacityOutcome.INDETERMINATE,
        reason_code=CapacityReasonCode.METRIC_UNSUPPORTED,
        enforcement=EnforcementMode.ADVISORY,
        observed_at=None,
    )


def _resolve_window(
    window_start: datetime | None,
    window_end: datetime | None,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Return the consumption window, defaulting when the declaration omits one."""
    start = window_start or now
    end = window_end or (start + timedelta(hours=_DEFAULT_WINDOW_HOURS))
    if end <= start:
        end = start + timedelta(hours=_DEFAULT_WINDOW_HOURS)
    return start, end


def _committed_reservation(partition_name: str, metric_name: str, start: datetime, end: datetime) -> float:
    """Sum open reservations overlapping ``[start, end)`` for one partition metric.

    Half-open comparison: two windows that merely touch at an instant do not
    contend, but any real overlap does.
    """
    from django.db.models import Sum

    from engine.models import CapacityReservation

    total = CapacityReservation.objects.filter(
        partition_name=partition_name,
        metric_name=metric_name,
        released_at__isnull=True,
        window_start__lt=end,
        window_end__gt=start,
    ).aggregate(total=Sum("amount"))["total"]
    return float(total or 0.0)


def _persist_assessment(event_ref: UUID, result: CapacityAssessmentResult) -> Any:
    """Append the immutable assessment snapshot.

    Verdicts are serialized as bounded codes only -- no limit, usage, account,
    or provider payload reaches this row.
    """
    from engine.models import CapacityAssessment

    return CapacityAssessment.objects.create(
        event_ref=event_ref,
        partition_name=result.partition.name,
        policy_version=result.policy_version,
        outcome=result.outcome.value,
        observed_at=result.observed_at,
        verdicts=[
            {
                "metric": verdict.metric_name,
                "outcome": verdict.outcome.value,
                "reason_code": verdict.reason_code.value,
                "enforcement": verdict.enforcement.value,
            }
            for verdict in result.verdicts
        ],
    )


def _persist_reservations(
    assessment: Any,
    event_ref: UUID,
    partition_name: str,
    demand: Mapping[str, float],
    specs: Mapping[str, CapacityMetricSpec],
    start: datetime,
    end: datetime,
    ranges: int = 1,
) -> None:
    """Commit this event's share of each declared metric for the window."""
    from engine.models import CapacityReservation

    CapacityReservation.objects.bulk_create(
        [
            CapacityReservation(
                assessment=assessment,
                event_ref=event_ref,
                partition_name=partition_name,
                metric_name=metric_name,
                amount=float(amount),
                # What one range draws when admitted, and the enforcement mode
                # in force when the budget was sized -- both pinned here so a
                # later draw reads the policy that produced the budget rather
                # than re-deriving it against drifted configuration.
                unit_amount=_unit_amount(specs[metric_name], ranges),
                enforcement=specs[metric_name].enforcement.value,
                window_start=start,
                window_end=end,
            )
            for metric_name, amount in sorted(demand.items())
            if metric_name in specs
        ]
    )


def _unit_amount(spec: CapacityMetricSpec, ranges: int) -> float:
    """Return the share of a metric one range consumes.

    Derived from the same declared shape that produced the budget, so the
    per-range draw and the event total stay consistent.
    """
    if spec.per_range_cost:
        return float(spec.per_range_cost)
    return 0.0


def _record_unassessable(
    *,
    event_ref: UUID,
    partition_name: str,
    policy_version: str,
    demand: Mapping[str, float],
    now: datetime,
    reason_code: CapacityReasonCode,
) -> CapacityAssessmentResult:
    """Record an assessment that could not be performed at all.

    Still persisted, because "we could not assess this event" is exactly the
    thing an operator needs to see afterwards.
    """
    result = CapacityAssessmentResult(
        partition=replace(_UNKNOWN_PARTITION, name=partition_name),
        policy_version=policy_version,
        observed_at=now,
        verdicts=tuple(
            MetricVerdict(
                metric_name=metric_name,
                outcome=CapacityOutcome.INDETERMINATE,
                reason_code=reason_code,
                enforcement=EnforcementMode.ADVISORY,
                observed_at=None,
            )
            for metric_name in sorted(demand)
        ),
    )
    _persist_assessment(event_ref, result)
    return result


def assess_declared_event_capacity(
    event_ref: UUID,
    *,
    partition_name: str | None = None,
    catalog: CapacityCatalog | None = None,
    inventory: Any = None,
    now: datetime | None = None,
) -> CapacityAssessmentResult | None:
    """Assess an event's newest capacity declaration.

    The product-facing entry point: CTF declares intent, then asks the Engine
    whether that intent fits. Returns ``None`` when the layer is disabled or the
    event has no declaration yet -- callers treat that as "no opinion" and
    proceed, so enabling capacity planning is a deliberate deployment step
    rather than a silent behaviour change.

    Demand is derived from the declaration's expected concurrent ranges and
    per-range shape, scaled by the deployment-declared cost coefficients in the
    catalog. Shifter never guesses what a range costs.
    """
    from django.conf import settings
    from django.utils import timezone

    from engine.services._capacity import latest_capacity_declaration

    if not getattr(settings, "CAPACITY_PLANNING_ENABLED", False):
        return None

    declaration = latest_capacity_declaration(event_ref)
    if declaration is None:
        return None

    resolved_catalog = catalog if catalog is not None else getattr(settings, "CAPACITY_PLANNING_CATALOG", None)
    if resolved_catalog is None:
        return None
    target = partition_name or str(getattr(settings, "CAPACITY_PLANNING_DEFAULT_PARTITION", ""))

    demand = _demand_from_declaration(declaration, resolved_catalog, target)
    if inventory is None:
        from shared.cloud import get_capacity_inventory

        try:
            inventory = get_capacity_inventory()
        except Exception:
            # No adapter for this backend: unmeasurable, never assumed-available.
            logger.warning("capacity: no inventory adapter available for event %s", event_ref)
            inventory = _NullInventory()

    return assess_event_capacity(
        event_ref=event_ref,
        partition_name=target,
        demand=demand.amounts,
        window_start=declaration.window_start,
        window_end=declaration.window_end,
        catalog=resolved_catalog,
        inventory=inventory,
        now=now or timezone.now(),
    )


class _NullInventory:
    """Inventory used when no adapter exists: everything is unmeasurable."""

    def observe(self, spec: CapacityMetricSpec, partition: PartitionRef) -> Any:
        from shared.capacity import ObservationResult

        return ObservationResult(reason_code=CapacityReasonCode.METRIC_UNSUPPORTED)


def _demand_from_declaration(declaration: Any, catalog: CapacityCatalog, partition_name: str) -> Any:
    """Scale a declaration's shape into per-metric demand for one partition."""
    from shared.capacity import build_demand

    specs = catalog.metrics_for(partition_name)
    hints = declaration.resource_hints if isinstance(declaration.resource_hints, dict) else {}
    agents_by_os = hints.get("agents_by_os")
    # Nodes per range: the authored agent mix. Untrusted organizer JSON, so
    # non-integer or negative entries are ignored rather than trusted.
    nodes_per_range = 0
    if isinstance(agents_by_os, dict):
        nodes_per_range = sum(
            value
            for value in agents_by_os.values()
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        )

    images = hints.get("images") if isinstance(hints.get("images"), dict) else {}
    return build_demand(
        partition_name=partition_name,
        expected_concurrent_ranges=max(0, int(declaration.expected_concurrent_ranges or 0)),
        nodes_per_range=nodes_per_range,
        per_range_costs={spec.name: spec.per_range_cost for spec in specs if spec.per_range_cost},
        per_node_costs={spec.name: spec.per_node_cost for spec in specs if spec.per_node_cost},
        images_per_range=_image_counts(images.get("per_range")),
        shared_images=_image_counts(images.get("shared")),
    )


def _image_counts(entries: Any) -> tuple[Any, ...]:
    """Rebuild image counts from the declaration's JSON hint.

    The hint is server-derived but still travels through a ``JSONField``, so
    each entry is shape-checked before use; a malformed entry is dropped rather
    than trusted into a pre-bake number.
    """
    from shared.capacity import ImageCount

    if not isinstance(entries, list):
        return ()
    counts = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        count = entry.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            continue
        counts.append(
            ImageCount(
                source_name=str(entry.get("source_name") or ""),
                source_version=str(entry.get("source_version") or ""),
                os_family=str(entry.get("os_family") or ""),
                count=count,
            )
        )
    return tuple(counts)


def _audit_assessment(assessment: Any, result: CapacityAssessmentResult) -> None:
    """Record the decision in the audit trail; never raises into provisioning.

    Bounded by design: the partition name, the outcome, the policy version, and
    the per-metric reason codes. No observed limit, usage figure, account
    identifier, or provider payload reaches audit free text.
    """
    try:
        from shared.audit import AuditAction, audit_log_system_event

        audit_log_system_event(
            entity_type="capacity_assessment",
            entity_id=assessment.pk,
            action=AuditAction.CAPACITY_ASSESS,
            source="engine.services.capacity",
            context=(
                f"partition={result.partition.name} outcome={result.outcome.value} "
                f"policy={result.policy_version} "
                f"codes={','.join(sorted({v.reason_code.value for v in result.verdicts}))}"
            ),
        )
    except Exception:
        logger.warning("capacity: failed to write assessment audit record")
