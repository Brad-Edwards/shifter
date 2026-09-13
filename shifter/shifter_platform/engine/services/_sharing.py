"""Engine-owned model-access sharing facades (PLAT-202, M19, #2139).

These are the authorized, revision-fenced boundary through which both
catalog-sourced and management-API drafts become live policy. Engine PostgreSQL
is the sole authority: a definition only affects a range after it passes
``publish_sharing_binding`` here.

Key invariants enforced in this module:

* Publisher identity is server-derived. The ``authorized_publisher_ref`` carried
  in a submitted definition is overwritten with the authenticated caller's
  identity before the definition is sealed and persisted.
* Publish is an optimistic compare-and-set on the binding's definition revision,
  inside a locked transaction; a stale expectation is refused.
* A pool's stable financial-account identities survive routing revisions; a
  routing change may re-key allocation groups but never rewrites an account
  reference.
* Withdrawal (``drain_sharing_binding``) tombstones the binding, advances the
  membership fence synchronously, and preserves the pool and its account
  references (no refund, no reset, no cascade delete).
* Effective-policy resolution is delegated to the single pure compiler in
  ``shared.model_access.effective_policy``; unknown or stale membership denies.

Membership *resolution* (the CTF/CMS/identity/workspace adapters that compute
authoritative membership) belongs to #2140; this module owns the projection
record it writes through and the freshness/fence contract that reads it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from shared.model_access import (
    BindingMatch,
    EffectivePolicy,
    ModelAccessCatalog,
    ModelProfile,
    SharingBinding,
    SharingPool,
    compile_effective_policy,
    compute_digest,
    seal_sharing_binding,
)
from shared.model_access.core_models import MembershipMode, OwnedReference, SelectorKind, SharingFacet

from ._common import EngineError

logger = logging.getLogger(__name__)

_ACTIVE = "active"
_TOMBSTONED = "tombstoned"
_AUDIT_SOURCE = "engine.services.sharing"

# Which pool reference each shareable facet requires to be a complete definition.
_FACET_POOL_REQUIREMENT: dict[SharingFacet, str] = {
    SharingFacet.PROVIDER_IDENTITY: "provider_pool_ref",
    SharingFacet.CAPACITY: "capacity_account_ref",
    SharingFacet.ROUTING: "alias_affinities",
    SharingFacet.SPEND: "spend_account_refs",
    SharingFacet.RATE: "rate_account_refs",
    SharingFacet.CONCURRENCY: "concurrency_account_refs",
}


class SharingError(EngineError):
    """Bounded model-access sharing failure; carries a stable code, never a value."""

    def __init__(self, code: str, message: str | None = None) -> None:
        """Operation for init."""
        self.code = code
        super().__init__(message or code)


def _as_binding(binding: SharingBinding | dict) -> SharingBinding:
    """Coerce a payload or DTO into a validated ``SharingBinding``."""
    return binding if isinstance(binding, SharingBinding) else SharingBinding.model_validate(binding)


def _as_pool(pool: SharingPool | dict) -> SharingPool:
    """Coerce a payload or DTO into a validated ``SharingPool``."""
    return pool if isinstance(pool, SharingPool) else SharingPool.model_validate(pool)


def _as_ref(reference: OwnedReference | dict) -> OwnedReference:
    """Coerce a payload or DTO into a validated ``OwnedReference``."""
    return reference if isinstance(reference, OwnedReference) else OwnedReference.model_validate(reference)


def validate_sharing_binding(
    *,
    deployment_id: UUID,
    catalog: ModelAccessCatalog,
    binding: SharingBinding | dict,
    pool: SharingPool | dict,
) -> None:
    """Fail closed unless the binding and pool resolve against the pinned catalog.

    Confers no authority; publication rechecks everything under a lock.
    """
    binding = _as_binding(binding)
    pool = _as_pool(pool)

    if binding.deployment_id != deployment_id:
        raise SharingError("sharing.foreign_deployment")
    if catalog.deployment_id != deployment_id:
        raise SharingError("sharing.catalog_deployment_mismatch")
    if binding.sharing_pool_id != pool.sharing_pool_id:
        raise SharingError("sharing.pool_mismatch")

    catalog_profiles = {profile.profile_id for profile in catalog.profiles}
    catalog_aliases = {alias.logical_alias for alias in catalog.aliases}

    if binding.profile_id is not None and binding.profile_id not in catalog_profiles:
        raise SharingError("sharing.unknown_profile")
    for affinity in pool.alias_affinities:
        if affinity.logical_alias not in catalog_aliases:
            raise SharingError("sharing.unknown_alias")

    for facet in binding.facets:
        _require_facet_reference(facet, binding, pool)


def _require_facet_reference(facet: SharingFacet, binding: SharingBinding, pool: SharingPool) -> None:
    """Every selected facet must carry a concrete profile or pool reference."""
    if facet is SharingFacet.PROFILE:
        if binding.profile_id is None:
            raise SharingError("sharing.facet_without_reference")
        return
    attribute = _FACET_POOL_REQUIREMENT[facet]
    if not getattr(pool, attribute):
        raise SharingError("sharing.facet_without_reference")


def _resolve_catalog_profile(catalog: ModelAccessCatalog, profile_id: str | None) -> dict | None:
    """Return the catalog profile as a JSON snapshot to freeze at publication."""
    if profile_id is None:
        return None
    for profile in catalog.profiles:
        if profile.profile_id == profile_id:
            return profile.model_dump(mode="json")
    raise SharingError("sharing.unknown_profile")


def publish_sharing_binding(
    *,
    deployment_id: UUID,
    catalog: ModelAccessCatalog,
    binding: SharingBinding | dict,
    pool: SharingPool | dict,
    publisher_identity: OwnedReference | dict,
    expected_definition_revision: int,
    empty_snapshot_ack: bool = False,
    now: datetime | None = None,
):
    """Persist the next immutable definition revision under an optimistic fence."""
    from engine.models import (
        MembershipProjection,
        SharingBindingRecord,
        SharingBindingRevision,
    )

    binding = _as_binding(binding)
    pool = _as_pool(pool)
    publisher = _as_ref(publisher_identity)
    validate_sharing_binding(deployment_id=deployment_id, catalog=catalog, binding=binding, pool=pool)

    # The definition is re-sealed with the server-derived publisher so the stored
    # form and its digest can never carry a caller-asserted authority.
    definition_payload = binding.model_dump(mode="json")
    definition_payload["authorized_publisher_ref"] = publisher.model_dump(mode="json")
    sealed = seal_sharing_binding(definition_payload)

    # Pin the resolved profile and catalog digest so resolution can never re-read
    # a mutated catalog and silently change the published policy.
    resolved_profile = _resolve_catalog_profile(catalog, sealed.profile_id)
    # Membership evidence is bound to the exact canonical selector being published,
    # so editing the selector cannot reuse the previous selector's evidence.
    selector_digest = compute_digest(sealed.selector)
    moment = now or timezone.now()
    is_snapshot = sealed.membership_mode is MembershipMode.SNAPSHOT

    with transaction.atomic():
        pool_record = _upsert_pool(deployment_id, pool)
        record = (
            SharingBindingRecord.objects.select_for_update()
            .filter(deployment_id=deployment_id, sharing_binding_id=sealed.sharing_binding_id)
            .first()
        )
        current = record.current_definition_revision if record is not None else 0
        if expected_definition_revision != current:
            raise SharingError("sharing.revision_conflict")

        # Publication requires fresh, revision-matched authoritative membership
        # evidence for THIS selector; missing, unknown, stale or wrong-selector
        # evidence denies. A binding must never publish restrictions that
        # resolution cannot later prove apply to the selector it names.
        projection = (
            MembershipProjection.objects.select_for_update()
            .filter(
                deployment_id=deployment_id,
                sharing_binding_id=sealed.sharing_binding_id,
                selector_digest=selector_digest,
            )
            .first()
        )
        if (
            projection is None
            or not projection.is_fresh(moment)
            or projection.membership_revision != sealed.membership_revision
        ):
            raise SharingError("sharing.membership_evidence_required")

        # Snapshot bindings freeze the resolved membership at publication; later
        # projection changes do not add or remove members without republishing.
        frozen_members = list(projection.member_refs) if is_snapshot else []
        if is_snapshot and not frozen_members and not empty_snapshot_ack:
            raise SharingError("sharing.empty_snapshot_unacknowledged")

        next_revision = current + 1
        if record is None:
            record = SharingBindingRecord.objects.create(
                deployment_id=deployment_id,
                sharing_binding_id=sealed.sharing_binding_id,
                pool=pool_record,
                current_definition_revision=next_revision,
                state=_ACTIVE,
            )
        else:
            record.pool = pool_record
            record.current_definition_revision = next_revision
            record.state = _ACTIVE
            record.save(update_fields=["pool", "current_definition_revision", "state", "updated_at"])

        revision = SharingBindingRevision.objects.create(
            binding=record,
            definition_revision=next_revision,
            definition_digest=sealed.definition_digest,
            definition=sealed.model_dump(mode="json"),
            catalog_digest=catalog.digest,
            resolved_profile=resolved_profile,
            frozen_members=frozen_members,
            pool_routing_revision=pool.routing_revision,
            selector_digest=selector_digest,
            membership_mode=sealed.membership_mode.value,
            priority=sealed.priority,
            effective_from=sealed.effective_from,
            effective_until=sealed.effective_until,
            observed_membership_revision=projection.membership_revision,
            publisher_owner=publisher.owner,
            publisher_reference=publisher.reference,
            empty_snapshot_ack=empty_snapshot_ack,
            state=_ACTIVE,
        )

    _audit(
        "sharing_publish",
        entity_id=record.pk,
        context=(
            f"binding={sealed.sharing_binding_id} revision={next_revision} "
            f"pool={sealed.sharing_pool_id} priority={sealed.priority} "
            f"facets={','.join(facet.value for facet in sealed.facets)}"
        ),
    )
    return revision


def _upsert_pool(deployment_id: UUID, pool: SharingPool):
    """Create or update the pool, keeping account identity stable and routing immutable.

    Financial-account identity is the pool's stable identity and survives routing
    revisions. Routing content (provider pool + per-alias affinity) is versioned in
    immutable ``SharingPoolRevision`` rows: an existing revision must carry
    identical content, and a new revision is appended, so a binding pinned to an
    earlier revision keeps its routing choice.
    """
    from engine.models import SharingPoolRecord, SharingPoolRevision

    payload = pool.model_dump(mode="json")
    provider = payload["provider_pool_ref"] or ""
    capacity = payload["capacity_account_ref"] or ""
    routing_revision = payload["routing_revision"]

    record = (
        SharingPoolRecord.objects.select_for_update()
        .filter(deployment_id=deployment_id, sharing_pool_id=pool.sharing_pool_id)
        .first()
    )
    if record is None:
        record = SharingPoolRecord.objects.create(
            deployment_id=deployment_id,
            sharing_pool_id=pool.sharing_pool_id,
            routing_revision=routing_revision,
            provider_pool_ref=provider,
            capacity_account_ref=capacity,
            spend_account_refs=payload["spend_account_refs"],
            rate_account_refs=payload["rate_account_refs"],
            concurrency_account_refs=payload["concurrency_account_refs"],
            alias_affinities=payload["alias_affinities"],
        )
        SharingPoolRevision.objects.create(
            pool=record,
            routing_revision=routing_revision,
            provider_pool_ref=provider,
            alias_affinities=payload["alias_affinities"],
        )
        return record

    # Financial-account identity can never be rewritten on an existing pool.
    stable_new = (
        capacity,
        payload["spend_account_refs"],
        payload["rate_account_refs"],
        payload["concurrency_account_refs"],
    )
    stable_existing = (
        record.capacity_account_ref,
        record.spend_account_refs,
        record.rate_account_refs,
        record.concurrency_account_refs,
    )
    if stable_new != stable_existing:
        raise SharingError("sharing.account_identity_changed")
    if routing_revision < record.routing_revision:
        raise SharingError("sharing.routing_revision_regressed")

    # A routing revision must identify a stable routing choice: an existing
    # revision must be byte-identical; a new revision is appended immutably.
    existing_revision = SharingPoolRevision.objects.filter(pool=record, routing_revision=routing_revision).first()
    routing_content = (provider, payload["alias_affinities"])
    if existing_revision is not None:
        if (existing_revision.provider_pool_ref, existing_revision.alias_affinities) != routing_content:
            raise SharingError("sharing.routing_content_changed")
    else:
        SharingPoolRevision.objects.create(
            pool=record,
            routing_revision=routing_revision,
            provider_pool_ref=provider,
            alias_affinities=payload["alias_affinities"],
        )

    if routing_revision > record.routing_revision:
        record.routing_revision = routing_revision
        record.provider_pool_ref = provider
        record.alias_affinities = payload["alias_affinities"]
        record.save(update_fields=["routing_revision", "provider_pool_ref", "alias_affinities", "updated_at"])
    return record


def _pinned_pool(record, routing_revision: int) -> SharingPool:
    """Reconstruct a SharingPool DTO for the pinned routing revision + stable accounts."""
    from engine.models import SharingPoolRevision

    pool_revision = SharingPoolRevision.objects.filter(pool=record, routing_revision=routing_revision).first()
    provider = pool_revision.provider_pool_ref if pool_revision is not None else record.provider_pool_ref
    alias_affinities = pool_revision.alias_affinities if pool_revision is not None else record.alias_affinities
    return SharingPool.model_validate(
        {
            "sharing_pool_id": record.sharing_pool_id,
            "routing_revision": routing_revision,
            "alias_affinities": alias_affinities,
            "provider_pool_ref": provider or None,
            "capacity_account_ref": record.capacity_account_ref or None,
            "spend_account_refs": record.spend_account_refs,
            "rate_account_refs": record.rate_account_refs,
            "concurrency_account_refs": record.concurrency_account_refs,
        }
    )


def drain_sharing_binding(
    *,
    deployment_id: UUID,
    sharing_binding_id: str,
    publisher_identity: OwnedReference | dict,
    expected_definition_revision: int,
    now: datetime | None = None,
):
    """Withdraw a binding: publish a terminal revision and fence its projections.

    Preserves the pool and every account reference; drain of the underlying
    liabilities is asynchronous and does not refund or reset any account here.
    """
    from engine.models import MembershipProjection, SharingBindingRecord, SharingBindingRevision

    publisher = _as_ref(publisher_identity)
    with transaction.atomic():
        record = (
            SharingBindingRecord.objects.select_for_update()
            .filter(deployment_id=deployment_id, sharing_binding_id=sharing_binding_id)
            .first()
        )
        if record is None:
            raise SharingError("sharing.binding_not_found")
        if expected_definition_revision != record.current_definition_revision:
            raise SharingError("sharing.revision_conflict")

        prior = record.revisions.get(definition_revision=record.current_definition_revision)
        next_revision = record.current_definition_revision + 1
        terminal = SharingBindingRevision.objects.create(
            binding=record,
            definition_revision=next_revision,
            definition_digest=prior.definition_digest,
            definition=prior.definition,
            catalog_digest=prior.catalog_digest,
            resolved_profile=prior.resolved_profile,
            frozen_members=prior.frozen_members,
            pool_routing_revision=prior.pool_routing_revision,
            selector_digest=prior.selector_digest,
            membership_mode=prior.membership_mode,
            priority=prior.priority,
            effective_from=prior.effective_from,
            effective_until=prior.effective_until,
            observed_membership_revision=prior.observed_membership_revision,
            publisher_owner=publisher.owner,
            publisher_reference=publisher.reference,
            empty_snapshot_ack=prior.empty_snapshot_ack,
            state=_TOMBSTONED,
        )
        record.current_definition_revision = next_revision
        record.state = _TOMBSTONED
        record.save(update_fields=["current_definition_revision", "state", "updated_at"])

        # Advance the invalidation fence synchronously so requests see the
        # withdrawal immediately, before any asynchronous reassessment.
        for projection in MembershipProjection.objects.select_for_update().filter(
            deployment_id=deployment_id, sharing_binding_id=sharing_binding_id
        ):
            projection.fence_revision += 1
            projection.save(update_fields=["fence_revision", "updated_at"])

    _audit(
        "sharing_drain",
        entity_id=record.pk,
        context=f"binding={sharing_binding_id} revision={next_revision}",
    )
    return terminal


def publish_membership_projection(
    *,
    deployment_id: UUID,
    sharing_binding_id: str,
    selector_digest: str,
    membership_revision: int,
    state: str,
    member_refs: list[str],
    observed_at: datetime,
    freshness_deadline: datetime,
):
    """Write an authoritative membership projection (the seam #2140 publishes through).

    Keyed by ``selector_digest`` so evidence for a new selector version is a
    distinct row and never clobbers the evidence the active definition resolves
    against, even when a subsequent publication fails.
    """
    from engine.models import MembershipProjection

    with transaction.atomic():
        projection, _created = MembershipProjection.objects.select_for_update().update_or_create(
            deployment_id=deployment_id,
            sharing_binding_id=sharing_binding_id,
            selector_digest=selector_digest,
            defaults={
                "membership_revision": membership_revision,
                "state": state,
                "member_refs": list(member_refs),
                "observed_at": observed_at,
                "freshness_deadline": freshness_deadline,
            },
        )

    _audit(
        "sharing_membership",
        entity_id=projection.pk,
        context=(
            f"binding={sharing_binding_id} membership_revision={membership_revision} "
            f"state={state} members={len(member_refs)}"
        ),
    )
    return projection


def preview_effective_policy(
    *,
    deployment_id: UUID,
    catalog: ModelAccessCatalog,
    subject: OwnedReference | dict,
    evaluated_at: datetime | None = None,
    now: datetime | None = None,
) -> EffectivePolicy:
    """Compile the effective policy for one subject; a preview is not authority."""
    from engine.models import SharingBindingRecord

    subject_ref = _as_ref(subject)
    moment = now or timezone.now()
    evaluated = evaluated_at or moment

    matches: list[BindingMatch] = []
    active = SharingBindingRecord.objects.filter(deployment_id=deployment_id, state=_ACTIVE).select_related("pool")
    for record in active:
        revision = record.revisions.filter(
            definition_revision=record.current_definition_revision, state=_ACTIVE
        ).first()
        if revision is None:
            continue
        match = _match_for_subject(record, revision, subject_ref, moment, catalog.digest)
        if match is not None:
            matches.append(match)

    return compile_effective_policy(
        deployment_id=deployment_id,
        catalog_digest=catalog.digest,
        evaluated_at=evaluated,
        subject=subject_ref,
        matches=tuple(matches),
    )


def _match_for_subject(record, revision, subject_ref: OwnedReference, moment: datetime, catalog_digest: str):
    """Build the BindingMatch for one active binding, or None when it does not apply.

    Uses the profile and (for snapshot) membership frozen at publication so
    resolution never re-reads a mutated catalog or a shifted live projection; a
    replaced catalog (digest mismatch) or a stale/missing projection fails closed.
    """
    from engine.models import MembershipProjection

    binding = SharingBinding.model_validate(revision.definition)
    catalog_ok = revision.catalog_digest == catalog_digest
    profile = (
        ModelProfile.model_validate(revision.resolved_profile)
        if revision.resolved_profile and SharingFacet.PROFILE in binding.facets
        else None
    )
    kind = binding.selector.kind

    if binding.membership_mode is MembershipMode.SNAPSHOT:
        if subject_ref.reference not in revision.frozen_members:
            return None
        membership_revision = revision.observed_membership_revision
        membership_fresh = catalog_ok
    else:
        # Evidence is bound to the selector this active revision published.
        projection = MembershipProjection.objects.filter(
            deployment_id=record.deployment_id,
            sharing_binding_id=record.sharing_binding_id,
            selector_digest=revision.selector_digest,
        ).first()
        if projection is None:
            # Published bindings always carry evidence; a missing projection is a
            # broken invariant. An all_ranges binding is unconditionally applicable
            # and so must fail closed; an explicit one cannot be shown to apply.
            if kind is not SelectorKind.ALL_RANGES:
                return None
            membership_revision = binding.membership_revision
            membership_fresh = False
        else:
            if not (kind is SelectorKind.ALL_RANGES or subject_ref.reference in projection.member_refs):
                return None
            membership_revision = projection.membership_revision
            membership_fresh = projection.is_fresh(moment) and catalog_ok

    return BindingMatch(
        binding=binding,
        pool=_pinned_pool(record.pool, revision.pool_routing_revision),
        profile=profile,
        membership_revision=membership_revision,
        membership_fresh=membership_fresh,
        matched_reason=kind.value,
    )


def get_or_create_allocation_group(
    *,
    deployment_id: UUID,
    sharing_pool_id: str,
    routing_revision: int,
    affinity: str,
    owner_ref: str | None = None,
) -> UUID:
    """Return the stable allocation-group UUID for a shared assignment key.

    ``per_range`` uses the existing draw UUID and has no group here; ``per_pool``
    keys on the routing revision alone, ``per_user`` on the canonical owner.
    """
    from engine.models import AllocationGroup

    if affinity not in ("per_user", "per_pool"):
        raise SharingError("sharing.unsupported_affinity")
    resolved_owner = "" if affinity == "per_pool" else (owner_ref or "")
    if affinity == "per_user" and not resolved_owner:
        raise SharingError("sharing.owner_required")

    group, _created = AllocationGroup.objects.get_or_create(
        deployment_id=deployment_id,
        sharing_pool_id=sharing_pool_id,
        routing_revision=routing_revision,
        affinity=affinity,
        owner_ref=resolved_owner,
    )
    return group.allocation_group_id


def _audit(action: str, *, entity_id: int, context: str) -> None:
    """Record a bounded audit event; never raises into the operation."""
    try:
        from shared.audit import audit_log_system_event

        audit_log_system_event(
            entity_type="sharing_binding",
            entity_id=entity_id,
            action=action,
            source=_AUDIT_SOURCE,
            context=context,
        )
    except Exception:
        logger.warning("sharing: failed to write audit record")
