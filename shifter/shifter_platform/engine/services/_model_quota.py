"""Ordered real-quota locking and exact model reservation/draw effects."""

from collections import defaultdict

from django.db.models import Sum
from django.utils import timezone

from engine.models import CapacityAssessment, ModelCapacityDraw, ModelCapacityReservation, ModelQuotaIdentity
from shared.model_access import ContractError, compute_digest


def lock_quotas(catalog, profile_id=None, allowed_shards=None, retained_ids=()):
    """Lock identities rather than possibly empty reservation queries."""
    identities = {}
    candidates = {
        shard_id
        for alias in catalog.aliases
        if profile_id is None or alias.profile_id == profile_id
        for shard_id in alias.eligible_shard_ids
    }
    if allowed_shards is not None:
        candidates &= allowed_shards
    required = {pool_id for shard in catalog.shards if shard.shard_id in candidates for pool_id in shard.quota_pool_ids}
    for pool in catalog.quota_pools:
        if pool.quota_pool_id not in required:
            continue
        fields = {
            "deployment_id": str(catalog.deployment_id),
            "provider_adapter_id": pool.provider_adapter_id,
            "provider_quota_identity": pool.provider_quota_identity,
            "dimension": pool.dimension,
            "unit": pool.unit,
        }
        identities[pool.quota_pool_id] = (compute_digest(fields), fields)
    locked = {}
    identities.update({f"retained:{identity}": (identity, None) for identity in retained_ids})
    for name, (identity, fields) in sorted(identities.items(), key=lambda item: item[1][0]):
        if fields is not None:
            ModelQuotaIdentity.objects.get_or_create(identity=identity, defaults=fields)
        locked[name] = ModelQuotaIdentity.objects.select_for_update().get(identity=identity)
    return locked


def metric_amount(pool, demand):
    """Map only supported exact dimensions and units; unknown means no admission."""
    values = {
        ("requests", "requests/minute"): demand.per_participant_requests,
        ("input_tokens", "tokens/minute"): demand.per_participant_input_tokens,
        ("output_tokens", "tokens/minute"): demand.per_participant_output_tokens,
        ("concurrency", "requests"): 1,
    }
    value = values.get((pool.dimension, pool.unit))
    if value is None or not 0 < value <= 2**63 - 1:
        raise ContractError("allocation.unsupported_metric")
    return value


def capacity_scope(request, effective):
    """Overlapping selectors coalesce at the stable capacity-account identity."""
    if effective.capacity_account_refs:
        return tuple(f"shared:{ref}" for ref in sorted(set(effective.capacity_account_refs)))
    # A replacement has a distinct dedicated commitment. A released original
    # remains a tombstone; it must never be revived for the successor.
    generation = f":{request.operation_id}" if request.scope_kind in {"standalone", "warm"} else ""
    return (f"{request.scope_kind}:{request.scope_id}{generation}",)


def _parent(quota, scope, request):
    return ModelCapacityReservation.objects.filter(
        quota=quota,
        scope_key=scope,
        window_start=request.window_start,
        window_end=request.window_end,
    ).first()


def _observed_headroom(pool, observation, catalog_digest, now):
    if observation is None or observation.catalog_digest != catalog_digest:
        return None
    if not observation.observed_at <= now < observation.valid_until:
        return None
    return min(pool.limit, observation.limit) - observation.usage


def vector_fits(vector, *, request, effective, catalog, locked, observations):
    """Recheck freshness after waiting for locks, then every parent and real pool."""
    now = timezone.now()
    if now >= request.window_end:
        raise ContractError("allocation.expired")
    pools = {pool.quota_pool_id: pool for pool in catalog.quota_pools}
    scopes = capacity_scope(request, effective)
    factor = (
        1
        if request.scope_kind == "standalone" and not effective.capacity_account_refs
        else request.demand.expected_concurrency
    )
    for pool_id, amount in vector.items():
        quota = locked[pool_id]
        headroom = _observed_headroom(pools[pool_id], observations.get(pool_id), catalog.digest, now)
        if headroom is None:
            return False
        added = 0
        for scope in scopes:
            parent = _parent(quota, scope, request)
            if parent is None:
                added += amount * factor
            else:
                budget = parent.workload_budgets.get(request.need.workload_role)
                if parent.released_at is not None:
                    return False
                if budget is None:
                    added += amount * factor
                elif budget["consumed"] + amount > budget["amount"]:
                    return False
        committed = (
            ModelCapacityReservation.objects.filter(
                quota=quota,
                released_at__isnull=True,
                window_start__lt=request.window_end,
                window_end__gt=request.window_start,
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        if added > 2**63 - 1 or committed + added > headroom:
            return False
    return True


def add_shard_demand(vector, shard, catalog, demand):
    """Distinct alias demands sum once on each physical metric."""
    updated = defaultdict(int, vector)
    pools = {pool.quota_pool_id: pool for pool in catalog.quota_pools}
    for pool_id in shard.quota_pool_ids:
        updated[pool_id] += metric_amount(pools[pool_id], demand)
    return dict(updated)


def persist_draws(allocation, vector, *, request, effective, catalog, locked, observations):
    """Write the whole effect vector in the allocation caller's transaction."""
    scopes = capacity_scope(request, effective)
    factor = (
        1
        if request.scope_kind == "standalone" and not effective.capacity_account_refs
        else request.demand.expected_concurrency
    )
    assessment = CapacityAssessment.objects.create(
        # ADR-047's opaque upstream scope reference also accepts an explicitly
        # typed non-event UUID. The typed scope is retained on each commitment.
        event_ref=request.scope_id,
        partition_name="model-access",
        policy_version=catalog.digest.removeprefix("sha256:"),
        outcome="admitted",
        observed_at=min(observations[key].observed_at for key in vector),
        verdicts=[
            {"metric": key, "outcome": "admitted", "reason_code": "available", "enforcement": "enforcing"}
            for key in sorted(vector)
        ],
    )
    for pool_id, amount in sorted(vector.items(), key=lambda item: locked[item[0]].pk):
        for scope in scopes:
            parent = _parent(locked[pool_id], scope, request)
            if parent is None:
                parent = ModelCapacityReservation.objects.create(
                    quota=locked[pool_id],
                    assessment=assessment,
                    scope_key=scope,
                    window_start=request.window_start,
                    window_end=request.window_end,
                    amount=amount * factor,
                    observation=observations[pool_id].model_dump(mode="json"),
                    workload_budgets={request.need.workload_role: {"amount": amount * factor, "consumed": 0}},
                )
            elif request.need.workload_role not in parent.workload_budgets:
                parent.amount += amount * factor
                parent.workload_budgets[request.need.workload_role] = {"amount": amount * factor, "consumed": 0}
            parent.workload_budgets[request.need.workload_role]["consumed"] += amount
            parent.consumed += amount
            parent.save(update_fields=["amount", "consumed", "workload_budgets"])
            ModelCapacityDraw.objects.create(allocation=allocation, reservation=parent, amount=amount)
