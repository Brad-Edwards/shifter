"""Internal persistence helpers for the model-access sharing facades (PLAT-202, M19).

Split out of ``engine.services._sharing`` so the public facade module and the
persistence/resolution helpers each stay small. The facades in ``_sharing`` call
these; nothing here imports ``_sharing`` back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.utils import timezone

from shared.model_access import (
    BindingMatch,
    ModelAccessCatalog,
    ModelProfile,
    SharingBinding,
    SharingPool,
    seal_sharing_binding,
)
from shared.model_access.core_models import MembershipMode, OwnedReference, SelectorKind, SharingFacet

from ._common import EngineError

if TYPE_CHECKING:
    from engine.models import (
        MembershipProjection,
        SharingBindingRecord,
        SharingBindingRevision,
        SharingPoolRecord,
    )

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


@dataclass(frozen=True)
class MembershipEvidence:
    """Authoritative membership a selector owner projects into Engine (the #2140 seam)."""

    membership_revision: int
    state: str
    member_refs: list[str]
    observed_at: datetime
    freshness_deadline: datetime


def _as_binding(binding: SharingBinding | dict) -> SharingBinding:
    """Coerce a payload or DTO into a validated ``SharingBinding``."""
    return binding if isinstance(binding, SharingBinding) else SharingBinding.model_validate(binding)


def _as_pool(pool: SharingPool | dict) -> SharingPool:
    """Coerce a payload or DTO into a validated ``SharingPool``."""
    return pool if isinstance(pool, SharingPool) else SharingPool.model_validate(pool)


def _as_ref(reference: OwnedReference | dict) -> OwnedReference:
    """Coerce a payload or DTO into a validated ``OwnedReference``."""
    return reference if isinstance(reference, OwnedReference) else OwnedReference.model_validate(reference)


def _seal_with_publisher(binding: SharingBinding, publisher: OwnedReference) -> SharingBinding:
    """Re-seal a binding with the server-derived publisher so its digest is authoritative."""
    payload = binding.model_dump(mode="json")
    payload["authorized_publisher_ref"] = publisher.model_dump(mode="json")
    return seal_sharing_binding(payload)


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


def _upsert_pool(deployment_id: UUID, pool: SharingPool) -> SharingPoolRecord:
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


def _require_membership_evidence(
    deployment_id: UUID, sharing_binding_id: str, selector_digest: str, membership_revision: int
) -> MembershipProjection:
    """Return the fresh, revision-matched projection for this selector, or deny."""
    from engine.models import MembershipProjection

    moment = timezone.now()
    projection = (
        MembershipProjection.objects.select_for_update()
        .filter(deployment_id=deployment_id, sharing_binding_id=sharing_binding_id, selector_digest=selector_digest)
        .first()
    )
    if projection is None or not projection.is_fresh(moment) or projection.membership_revision != membership_revision:
        raise SharingError("sharing.membership_evidence_required")
    return projection


def _frozen_snapshot(projection: MembershipProjection, is_snapshot: bool, empty_snapshot_ack: bool) -> list[str]:
    """Freeze snapshot membership at publication; an empty snapshot needs an explicit ack."""
    frozen = list(projection.member_refs) if is_snapshot else []
    if is_snapshot and not frozen and not empty_snapshot_ack:
        raise SharingError("sharing.empty_snapshot_unacknowledged")
    return frozen


def _write_binding_record(
    record: SharingBindingRecord | None,
    deployment_id: UUID,
    sealed: SharingBinding,
    pool_record: SharingPoolRecord,
    next_revision: int,
) -> SharingBindingRecord:
    """Create or advance the stable binding record to the next definition revision."""
    from engine.models import SharingBindingRecord

    if record is None:
        return SharingBindingRecord.objects.create(
            deployment_id=deployment_id,
            sharing_binding_id=sealed.sharing_binding_id,
            pool=pool_record,
            current_definition_revision=next_revision,
            state=_ACTIVE,
        )
    record.pool = pool_record
    record.current_definition_revision = next_revision
    record.state = _ACTIVE
    record.save(update_fields=["pool", "current_definition_revision", "state", "updated_at"])
    return record


def _pinned_pool(record: SharingPoolRecord, routing_revision: int) -> SharingPool:
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


def _match_for_subject(
    record: SharingBindingRecord,
    revision: SharingBindingRevision,
    subject_ref: OwnedReference,
    moment: datetime,
    catalog_digest: str,
) -> BindingMatch | None:
    """Build the BindingMatch for one active binding, or None when it does not apply.

    Uses the profile and (for snapshot) membership frozen at publication so
    resolution never re-reads a mutated catalog or a shifted live projection; a
    replaced catalog (digest mismatch) or a stale/missing projection fails closed.
    """
    binding = SharingBinding.model_validate(revision.definition)
    catalog_ok = revision.catalog_digest == catalog_digest
    if binding.membership_mode is MembershipMode.SNAPSHOT:
        resolved = _snapshot_applicability(revision, subject_ref, catalog_ok)
    else:
        resolved = _dynamic_applicability(record, revision, binding, subject_ref, moment, catalog_ok)
    if resolved is None:
        return None
    membership_revision, membership_fresh = resolved
    return BindingMatch(
        binding=binding,
        pool=_pinned_pool(record.pool, revision.pool_routing_revision),
        profile=_pinned_profile(revision, binding),
        membership_revision=membership_revision,
        membership_fresh=membership_fresh,
        matched_reason=binding.selector.kind.value,
    )


def _snapshot_applicability(
    revision: SharingBindingRevision, subject_ref: OwnedReference, catalog_ok: bool
) -> tuple[int, bool] | None:
    """Snapshot inclusion is frozen; only a replaced catalog invalidates it."""
    if subject_ref.reference not in revision.frozen_members:
        return None
    return revision.observed_membership_revision, catalog_ok


def _dynamic_applicability(
    record: SharingBindingRecord,
    revision: SharingBindingRevision,
    binding: SharingBinding,
    subject_ref: OwnedReference,
    moment: datetime,
    catalog_ok: bool,
) -> tuple[int, bool] | None:
    """Resolve dynamic applicability + freshness against the selector-bound projection."""
    from engine.models import MembershipProjection

    kind = binding.selector.kind
    projection = MembershipProjection.objects.filter(
        deployment_id=record.deployment_id,
        sharing_binding_id=record.sharing_binding_id,
        selector_digest=revision.selector_digest,
    ).first()
    if projection is None:
        # Published bindings always carry evidence; a missing projection is a
        # broken invariant. An all_ranges binding is unconditionally applicable and
        # so must fail closed; an explicit one cannot be shown to apply.
        return (binding.membership_revision, False) if kind is SelectorKind.ALL_RANGES else None
    if not (kind is SelectorKind.ALL_RANGES or subject_ref.reference in projection.member_refs):
        return None
    return projection.membership_revision, projection.is_fresh(moment) and catalog_ok


def _pinned_profile(revision: SharingBindingRevision, binding: SharingBinding) -> ModelProfile | None:
    """Return the profile frozen at publication when the binding shares a profile."""
    if revision.resolved_profile and SharingFacet.PROFILE in binding.facets:
        return ModelProfile.model_validate(revision.resolved_profile)
    return None


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
