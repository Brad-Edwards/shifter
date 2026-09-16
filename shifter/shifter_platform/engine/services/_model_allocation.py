"""Atomic model quota allocation, pinned routing and non-usable grant bindings."""

import logging
from datetime import datetime
from time import perf_counter

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError

from engine.models import ModelAliasAssignment, ModelAllocation, ModelAllocationAuthority, ModelPendingGrant, Range
from shared.model_access import ContractError, compute_digest, validate_catalog
from shared.model_access.allocation import ShardWeight, rank_weighted_rendezvous
from shared.model_access.core_models import AllocationStrategy
from shared.model_access.policy import intersect_profile
from shared.model_access.reservation import ModelAllocationRequest, ModelQuotaObservation

from ._model_allocation_authority import lock_assignment_groups, locked_policy
from ._model_quota import add_shard_demand, lock_quotas, persist_draws, vector_fits

logger = logging.getLogger(__name__)


def _validated(model, value):
    try:
        return model.model_validate(value.model_dump(mode="json") if isinstance(value, model) else value)
    except (ValueError, TypeError, ValidationError):
        raise ContractError("allocation.invalid_input") from None


def _lock_range(request):
    row = (
        Range.objects.select_for_update().filter(uuid=request.range_id, request__request_id=request.request_id).first()
    )
    if row is None or row.provisioner_operation_id != request.operation_id:
        raise ContractError("allocation.generation_mismatch")
    if request.owner_ref.owner != "management" or request.owner_ref.reference != f"user:{row.user_id}":
        raise ContractError("allocation.owner_mismatch")
    if row.status not in (Range.Status.PENDING, Range.Status.PROVISIONING, Range.Status.READY, Range.Status.RESUMING):
        raise ContractError("allocation.range_unavailable")
    if row.egress_mode in {"none", "deny-all"}:
        raise ContractError("allocation.egress_incompatible")
    if request.preparation_authority is not None and row.provisioner_operation != "raes-range:provision":
        raise ContractError("allocation.system_preparation_only")
    return row


def _replay(request, intent_digest):
    existing = ModelAllocation.objects.filter(
        request_id=request.request_id,
        operation_id=request.operation_id,
        workload_role=request.need.workload_role,
    ).first()
    if existing is None:
        return None
    if existing.intent_digest != intent_digest:
        raise ContractError("allocation.intent_conflict")
    if existing.released_at is not None or existing.grant.state != "pending" or timezone.now() >= existing.deadline:
        raise ContractError("allocation.revoked")
    return existing


def _effective_profile(request, catalog, sharing):
    profile = next((item for item in catalog.profiles if item.profile_id == request.need.profile_id), None)
    if profile is None or not catalog.enabled or not sharing.admissible:
        raise ContractError("allocation.policy_unavailable")
    effective = intersect_profile(profile, request.need)
    if effective is None:
        raise ContractError("allocation.policy_unavailable")
    if sharing.effective_profile is not None:
        other = sharing.effective_profile
        effective = effective.model_copy(
            update={
                "capabilities": tuple(sorted(set(effective.capabilities) & set(other.capabilities))),
                "data_regions": tuple(sorted(set(effective.data_regions) & set(other.data_regions))),
                "allowed_strategies": tuple(sorted(set(effective.allowed_strategies) & set(other.allowed_strategies))),
                "limits": effective.limits.tightened_with(other.limits),
            }
        )
    if not set(request.need.required_capabilities).issubset(effective.capabilities):
        raise ContractError("allocation.capability_unavailable")
    if request.demand.allowed_strategy not in effective.allowed_strategies:
        raise ContractError("allocation.strategy_not_allowed")
    return effective


def _rank(alias, request, catalog, policy_digest, group=None):
    if request.demand.allowed_strategy != alias.strategy:
        raise ContractError("allocation.strategy_not_allowed")
    candidates = [item for item in catalog.shards if item.shard_id in alias.eligible_shard_ids]
    if alias.strategy is AllocationStrategy.FIXED_V1:
        return candidates
    ranks = rank_weighted_rendezvous(
        deployment_id=catalog.deployment_id,
        allocation_group_id=group.allocation_group_id if group else request.draw_key,
        policy_digest=policy_digest,
        logical_alias=alias.logical_alias,
        shards=tuple(ShardWeight(item.shard_id, item.weight) for item in candidates),
    )
    by_id = {item.shard_id: item for item in candidates}
    return [by_id[item.shard_id] for item in ranks]


def _eligible(shard, profile, observations, now):
    return (
        shard.enabled
        and shard.region in profile.data_regions
        and set(profile.capabilities).issubset(shard.capabilities)
        and all(
            pool_id in observations
            and observations[pool_id].observed_at <= now < observations[pool_id].valid_until
            and shard.shard_id in observations[pool_id].healthy_shard_ids
            for pool_id in shard.quota_pool_ids
        )
    )


def _provider_candidates(catalog, sharing):
    """Intersect a declared provider pool; absent legacy mappings fail closed."""
    if sharing.provider_pool_ref is None:
        return {shard.shard_id for shard in catalog.shards}
    for pool in getattr(catalog, "provider_pools", ()):
        if pool.provider_pool_id == sharing.provider_pool_ref:
            return set(pool.shard_ids)
    raise ContractError("allocation.provider_pool_unavailable")


def _policy_digest(request, catalog, profile):
    return compute_digest(
        {
            "catalog": catalog.digest,
            "profile": profile.model_dump(mode="json"),
            "need": request.need.model_dump(mode="json"),
        }
    )


def _select(request, catalog, sharing, profile, locked, observations, groups):
    policy_digest = _policy_digest(request, catalog, profile)
    selected, vector = {}, {}
    aliases = [alias for alias in catalog.aliases if alias.profile_id == request.need.profile_id]
    if not aliases:
        raise ContractError("allocation.alias_unavailable")
    prices = {price.price_schedule_id: price for price in catalog.price_schedules}
    allowed_shards = _provider_candidates(catalog, sharing)
    for alias in aliases:
        price = prices[alias.price_schedule_id]
        if price.valid_until < request.window_end or price.currency != profile.limits.currency:
            raise ContractError("allocation.price_unavailable")
        group = groups.get(alias.logical_alias)
        assignment = (
            ModelAliasAssignment.objects.filter(group=group, logical_alias=alias.logical_alias).first()
            if group
            else None
        )
        candidates = [
            shard for shard in _rank(alias, request, catalog, policy_digest, group) if shard.shard_id in allowed_shards
        ]
        if assignment:
            candidates = [shard for shard in candidates if shard.model_dump(mode="json") == assignment.shard]
            if not candidates or not _eligible(candidates[0], profile, observations, timezone.now()):
                raise ContractError("allocation.shared_assignment_incompatible")
        for shard in candidates:
            if not _eligible(shard, profile, observations, timezone.now()):
                continue
            candidate = add_shard_demand(vector, shard, catalog, request.demand)
            if vector_fits(
                candidate, request=request, effective=sharing, catalog=catalog, locked=locked, observations=observations
            ):
                selected[alias.logical_alias] = shard
                vector = candidate
                if group and assignment is None:
                    ModelAliasAssignment.objects.create(
                        group=group,
                        logical_alias=alias.logical_alias,
                        shard=shard.model_dump(mode="json"),
                        policy_digest=policy_digest,
                    )
                break
        else:
            raise ContractError("allocation.capacity_unavailable")
    return selected, vector


def allocate_model_access(request, *, catalog, observations, retire_previous=False):
    """Record bounded admission timing without provider, prompt or identity labels."""
    started = perf_counter()
    try:
        allocation = _allocate_model_access(
            request, catalog=catalog, observations=observations, retire_previous=retire_previous
        )
    except ContractError:
        _admission_metric("denied", started)
        raise
    transaction.on_commit(lambda: _admission_metric("admitted", started))
    return allocation


def _admission_metric(outcome, started):
    """Use the existing structured logging transport; no network in the transaction."""
    logger.info(
        "model-access allocation",
        extra={
            "model_access_namespace": "Shifter/ModelAccess",
            "model_access_outcome": outcome,
            "model_access_elapsed_ms": (perf_counter() - started) * 1000,
        },
    )


def _allocate_model_access(request, *, catalog, observations, retire_previous=False):
    """Commit all alias effects or none; caller may join the launch transaction."""
    request = _validated(ModelAllocationRequest, request)
    observations = tuple(_validated(ModelQuotaObservation, item) for item in observations)
    observed = {item.quota_pool_id: item for item in observations}
    if len(observed) != len(observations):
        raise ContractError("allocation.duplicate_observation")
    intent_digest = compute_digest(request)
    with transaction.atomic():
        range_obj = _lock_range(request)
        existing = _replay(request, intent_digest)
        pinned_catalog = (
            validate_catalog(existing.snapshot["catalog"])
            if existing
            else validate_catalog(catalog.model_dump(mode="json"))
        )
        sharing, fences, revision_vector = locked_policy(request, pinned_catalog)
        if existing is not None:
            if existing.snapshot["revision_vector"] != revision_vector:
                raise ContractError("allocation.authority_changed")
            return existing
        catalog = pinned_catalog
        if catalog.deployment_id != request.deployment_id:
            raise ContractError("allocation.deployment_mismatch")
        profile = _effective_profile(request, catalog, sharing)
        policy_digest = _policy_digest(request, catalog, profile)
        groups = lock_assignment_groups(request, catalog, sharing)
        previous = (
            list(
                ModelAllocation.objects.filter(range_id=request.range_id, released_at__isnull=True).exclude(
                    operation_id=request.operation_id
                )
            )
            if retire_previous
            else []
        )
        old_quotas = {
            quota_id for prior in previous for quota_id in prior.draws.values_list("reservation__quota_id", flat=True)
        }
        locked = lock_quotas(catalog, request.need.profile_id, _provider_candidates(catalog, sharing), old_quotas)
        from ._model_allocation_lifecycle import release_model_allocation

        for prior in previous:
            release_model_allocation(prior.pk, operation_id=prior.operation_id)
        selected, vector = _select(request, catalog, sharing, profile, locked, observed, groups)
        # Locks preserve revisions, not the passage of time. Recheck the whole
        # decision after every group/quota wait and before writing any effect.
        now = timezone.now()
        if now >= datetime.fromisoformat(revision_vector["fresh_until"]):
            raise ContractError("allocation.authority_unavailable")
        if not all(_eligible(shard, profile, observed, now) for shard in selected.values()) or not vector_fits(
            vector, request=request, effective=sharing, catalog=catalog, locked=locked, observations=observed
        ):
            raise ContractError("allocation.capacity_unavailable")
        allocation = ModelAllocation.objects.create(
            deployment_id=request.deployment_id,
            request_id=request.request_id,
            operation_id=request.operation_id,
            range_id=request.range_id,
            draw_key=request.draw_key,
            workload_role=request.need.workload_role,
            intent_digest=intent_digest,
            policy_digest=policy_digest,
            deadline=min(request.window_end, datetime.fromisoformat(revision_vector["fresh_until"])),
            alias_shards={alias: shard.shard_id for alias, shard in selected.items()},
            snapshot={
                "request": request.model_dump(mode="json"),
                "need": request.need.model_dump(mode="json"),
                "catalog": catalog.model_dump(mode="json"),
                "effective_policy": sharing.model_dump(mode="json"),
                "profile": profile.model_dump(mode="json"),
                "revision_vector": revision_vector,
                "shards": {alias: shard.model_dump(mode="json") for alias, shard in selected.items()},
            },
        )
        previous_epoch = (
            ModelPendingGrant.objects.filter(allocation__range_id=request.range_id).aggregate(
                maximum=Max("grant_epoch")
            )["maximum"]
            or 0
        )
        ModelPendingGrant.objects.create(allocation=allocation, grant_epoch=previous_epoch + 1)
        ModelAllocationAuthority.objects.bulk_create(
            ModelAllocationAuthority(allocation=allocation, fence=fence, revision=fence.authority_revision)
            for fence in fences
        )
        persist_draws(
            allocation,
            vector,
            request=request,
            effective=sharing,
            catalog=catalog,
            locked=locked,
            observations=observed,
        )
        from shared.audit import AuditActorType, AuditEvent, audit_log

        audit_log(
            AuditEvent(
                entity_type="range",
                entity_id=range_obj.pk,
                action="capacity_assess",
                actor_type=AuditActorType.SYSTEM,
                context=f"model-access allocation={allocation.pk} operation={request.operation_id}",
            ),
            strict=True,
        )
        return allocation
