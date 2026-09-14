"""Engine-owned model-access sharing facades (PLAT-202, M19, #2139).

These are the authorized, revision-fenced boundary through which both
catalog-sourced and management-API drafts become live policy. Engine PostgreSQL
is the sole authority: a definition only affects a range after it passes
``publish_sharing_binding`` here.

Key invariants:

* Publisher identity is server-derived; a submitted ``authorized_publisher_ref``
  is overwritten before the definition is sealed and persisted.
* Publish is an optimistic compare-and-set on the definition revision inside a
  locked transaction, and requires fresh, selector-bound membership evidence.
* A pool's stable financial-account identity survives routing revisions; routing
  content is versioned in immutable pool routing revisions.
* Withdrawal tombstones the binding, advances the membership fence synchronously,
  and preserves the pool and its account references (no refund/reset/cascade).
* Effective-policy resolution is delegated to the single pure compiler in
  ``shared.model_access.effective_policy``; unknown or stale membership denies.

Internal persistence/resolution helpers live in ``_sharing_persistence``.
Membership *resolution* (the CTF/CMS/identity adapters) belongs to #2140; this
module owns the projection record it writes through and the freshness/fence gate.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from shared.model_access import (
    AuthorityInvalidation,
    AuthorityState,
    BindingMatch,
    EffectivePolicy,
    ModelAccessCatalog,
    ModelAccessRangePage,
    ModelAccessRangeView,
    OwnedReference,
    PublisherAuthorityEvidence,
    SelectorAuthorityEvidence,
    SelectorResolution,
    SharingAuthorityEvidence,
    SharingBinding,
    SharingPool,
    SpendingEligibilityEvidence,
    SubjectAuthorizationEvidence,
    compile_effective_policy,
    compute_digest,
)
from shared.model_access.core_models import MembershipMode

from ._sharing_persistence import (
    _ACTIVE,
    _TOMBSTONED,
    MembershipEvidence,
    SharingError,
    _as_binding,
    _as_evidence,
    _as_pool,
    _as_ref,
    _audit,
    _frozen_snapshot,
    _match_for_subject,
    _require_facet_reference,
    _require_membership_evidence,
    _require_projection_fences,
    _require_publisher_authority,
    _require_spending_eligibility,
    _resolve_catalog_profile,
    _seal_with_publisher,
    _upsert_pool,
    _write_binding_record,
)

if TYPE_CHECKING:
    from engine.models import MembershipProjection, SharingBindingRevision

__all__ = [
    "MembershipEvidence",
    "ModelAccessRangeView",
    "SharingError",
    "drain_sharing_binding",
    "get_or_create_allocation_group",
    "invalidate_sharing_authority",
    "preview_effective_policy",
    "project_selector_resolution",
    "publish_authority_fence",
    "publish_membership_projection",
    "publish_sharing_binding",
    "resolve_model_access_range_page",
    "resolve_model_access_range_views",
    "validate_sharing_binding",
]


def resolve_model_access_range_page(
    *,
    range_uuids: tuple[UUID, ...] | None = None,
    request_uuids: tuple[UUID, ...] | None = None,
    user_ids: tuple[int, ...] | None = None,
    workspace_ids: tuple[int, ...] | None = None,
    all_ranges: bool = False,
    continuation: UUID | None = None,
    page_size: int = 1000,
) -> ModelAccessRangePage:
    """Lock one bounded keyset page from an exact range-membership query.

    Explicit identity queries are exact: an unknown or terminal range makes the
    whole resolution fail closed. Automatic collections expose an assessment
    count and continuation so deployment growth never turns into a hard result
    ceiling. Collection queries may legitimately resolve to an empty set.
    """
    from engine.models import Range

    modes = (range_uuids, request_uuids, user_ids, workspace_ids)
    if sum(value is not None for value in modes) + int(all_ranges) != 1:
        raise SharingError("sharing.range_resolution_shape")

    explicit_values: tuple[object, ...] | None = None
    filters: dict[str, object] = {}
    if range_uuids is not None:
        explicit_values = tuple(range_uuids)
        filters["uuid__in"] = explicit_values
    elif request_uuids is not None:
        explicit_values = tuple(request_uuids)
        filters["request__request_id__in"] = explicit_values
    elif user_ids is not None:
        filters["user_id__in"] = tuple(user_ids)
    elif workspace_ids is not None:
        filters["workspace_id__in"] = tuple(workspace_ids)

    supplied = next((value for value in modes if value is not None), ())
    if len(supplied) != len(set(supplied)):
        raise SharingError("sharing.range_resolution_shape")
    if explicit_values is not None and len(supplied) > 1000:
        raise SharingError("sharing.range_resolution_shape")
    if any(isinstance(value, bool) for value in supplied):
        raise SharingError("sharing.range_resolution_shape")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 1000
        or (continuation is not None and not isinstance(continuation, UUID))
        or (explicit_values is not None and continuation is not None)
        or (explicit_values is not None and page_size < len(explicit_values))
    ):
        raise SharingError("sharing.range_resolution_shape")

    unavailable = (Range.Status.DESTROYING, Range.Status.DESTROYED, Range.Status.FAILED)
    with transaction.atomic():
        eligible = Range.objects.filter(**filters).exclude(status__in=unavailable)
        assessment_count = eligible.count()
        if continuation is not None:
            eligible = eligible.filter(uuid__gt=continuation)
        page_rows = tuple(
            eligible.select_for_update(of=("self",)).select_related("request").order_by("uuid")[: page_size + 1]
        )
        if explicit_values is not None and assessment_count != len(explicit_values):
            raise SharingError("sharing.range_membership_unavailable")
        rows = page_rows[:page_size]
        items = tuple(
            ModelAccessRangeView(
                range_ref=OwnedReference(owner="deployment", reference=f"range:{row.uuid}"),
                authority_ref=OwnedReference(owner="engine", reference=f"range:{row.uuid}"),
                range_uuid=row.uuid,
                owner_user_id=row.user_id,
                workspace_id=row.workspace_id,
                request_uuid=row.request.request_id if row.request is not None else None,
            )
            for row in rows
        )
        return ModelAccessRangePage(
            items=items,
            assessment_count=assessment_count,
            continuation=items[-1].range_uuid if len(page_rows) > page_size else None,
        )


def resolve_model_access_range_views(
    *,
    range_uuids: tuple[UUID, ...] | None = None,
    request_uuids: tuple[UUID, ...] | None = None,
    user_ids: tuple[int, ...] | None = None,
    workspace_ids: tuple[int, ...] | None = None,
    all_ranges: bool = False,
) -> tuple[ModelAccessRangeView, ...]:
    """Resolve a complete collection through stable bounded keyset pages."""
    items: list[ModelAccessRangeView] = []
    continuation = None
    assessment_count = None
    while True:
        page = resolve_model_access_range_page(
            range_uuids=range_uuids,
            request_uuids=request_uuids,
            user_ids=user_ids,
            workspace_ids=workspace_ids,
            all_ranges=all_ranges,
            continuation=continuation,
        )
        if assessment_count is None:
            assessment_count = page.assessment_count
        elif page.assessment_count != assessment_count:
            raise SharingError("sharing.range_membership_changed")
        items.extend(page.items)
        if page.continuation is None:
            break
        if continuation == page.continuation:
            raise SharingError("sharing.range_membership_changed")
        continuation = page.continuation
    if len(items) != assessment_count:
        raise SharingError("sharing.range_membership_changed")
    return tuple(items)


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


def publish_sharing_binding(
    *,
    deployment_id: UUID,
    catalog: ModelAccessCatalog,
    binding: SharingBinding | dict,
    pool: SharingPool | dict,
    publisher_identity: OwnedReference | dict,
    expected_definition_revision: int,
    empty_snapshot_ack: bool = False,
) -> SharingBindingRevision:
    """Persist the next immutable definition revision under an optimistic fence."""
    from engine.models import SharingBindingRecord, SharingBindingRevision

    binding = _as_binding(binding)
    pool = _as_pool(pool)
    publisher = _as_ref(publisher_identity)
    validate_sharing_binding(deployment_id=deployment_id, catalog=catalog, binding=binding, pool=pool)

    # Re-seal with the server-derived publisher, and pin the resolved profile,
    # catalog digest and selector digest so resolution can never re-read a mutated
    # catalog or reuse another selector's membership evidence.
    sealed = _seal_with_publisher(binding, publisher)
    resolved_profile = _resolve_catalog_profile(catalog, sealed.profile_id)
    selector_digest = compute_digest(sealed.selector)
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

        projection = _require_membership_evidence(
            deployment_id, sealed.sharing_binding_id, selector_digest, sealed.membership_revision
        )
        publisher_authorities = _require_publisher_authority(
            deployment_id=deployment_id,
            projection=projection,
            binding=sealed,
            publisher=publisher,
            lock=False,
        )
        _require_spending_eligibility(
            deployment_id=deployment_id,
            projection=projection,
            binding=sealed,
            lock=False,
        )
        frozen_members = _frozen_snapshot(projection, is_snapshot, empty_snapshot_ack)

        next_revision = current + 1
        record = _write_binding_record(record, deployment_id, sealed, pool_record, next_revision)
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
            observed_assessment_count=projection.assessment_count,
            observed_authority_revisions=projection.selector_authorities,
            publisher_authority_revisions=[item.model_dump(mode="json") for item in publisher_authorities],
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


def drain_sharing_binding(
    *,
    deployment_id: UUID,
    sharing_binding_id: str,
    publisher_identity: OwnedReference | dict,
    expected_definition_revision: int,
) -> SharingBindingRevision:
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
        projection = (
            MembershipProjection.objects.select_for_update()
            .filter(
                deployment_id=deployment_id,
                sharing_binding_id=sharing_binding_id,
                selector_digest=prior.selector_digest,
            )
            .first()
        )
        if projection is None:
            raise SharingError("sharing.publisher_authority_required")
        binding = SharingBinding.model_validate(prior.definition)
        publisher_authorities = _require_publisher_authority(
            deployment_id=deployment_id,
            projection=projection,
            binding=binding,
            publisher=publisher,
        )
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
            observed_assessment_count=prior.observed_assessment_count,
            observed_authority_revisions=prior.observed_authority_revisions,
            publisher_authority_revisions=[item.model_dump(mode="json") for item in publisher_authorities],
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


def publish_authority_fence(
    *,
    deployment_id: UUID,
    authority_ref: OwnedReference | dict,
    authority_revision: int,
    state: AuthorityState | str,
):
    """Publish one monotonic shared authority revision inside the caller transaction."""
    from engine.models import SharingAuthorityFence

    reference = _as_ref(authority_ref)
    try:
        resolved_state = state if isinstance(state, AuthorityState) else AuthorityState(state)
    except ValueError as exc:
        raise SharingError("sharing.invalid_authority_state") from exc
    if isinstance(authority_revision, bool) or not isinstance(authority_revision, int) or authority_revision <= 0:
        raise SharingError("sharing.invalid_authority_revision")

    with transaction.atomic():
        fence = (
            SharingAuthorityFence.objects.select_for_update()
            .filter(
                deployment_id=deployment_id,
                authority_owner=reference.owner,
                authority_reference=reference.reference,
            )
            .first()
        )
        if fence is None:
            return SharingAuthorityFence.objects.create(
                deployment_id=deployment_id,
                authority_owner=reference.owner,
                authority_reference=reference.reference,
                authority_revision=authority_revision,
                state=resolved_state.value,
            )
        if authority_revision < fence.authority_revision:
            raise SharingError("sharing.authority_revision_regressed")
        if authority_revision == fence.authority_revision:
            if fence.state != resolved_state.value:
                raise SharingError("sharing.authority_revision_conflict")
            return fence
        fence.authority_revision = authority_revision
        fence.state = resolved_state.value
        fence.save(update_fields=["authority_revision", "state", "updated_at"])
        return fence


def invalidate_sharing_authority(command: AuthorityInvalidation | dict) -> int:
    """Synchronously advance every matching owner fence before bounded fan-out.

    A deployment-scoped command creates a missing fence so a mutation racing
    first publication is deny-authoritative.  A deployment-agnostic identity or
    workspace mutation advances every existing deployment row and creates
    nothing when no live projection has ever referenced that owner fact.
    """
    from engine.models import SharingAuthorityFence

    command = command if isinstance(command, AuthorityInvalidation) else AuthorityInvalidation.model_validate(command)
    wanted = Q()
    for reference in command.authority_refs:
        wanted |= Q(
            authority_owner=reference.owner,
            authority_reference=reference.reference,
        )

    changed = 0
    with transaction.atomic():
        rows_query = SharingAuthorityFence.objects.select_for_update().filter(wanted)
        if command.deployment_id is not None:
            rows_query = rows_query.filter(deployment_id=command.deployment_id)
        rows = list(rows_query.order_by("deployment_id", "authority_owner", "authority_reference"))
        existing = {(row.deployment_id, row.authority_owner, row.authority_reference): row for row in rows}
        for row in rows:
            row.authority_revision += 1
            row.state = command.state.value
            row.save(update_fields=["authority_revision", "state", "updated_at"])
            changed += 1

        if command.deployment_id is not None:
            for reference in command.authority_refs:
                key = (command.deployment_id, reference.owner, reference.reference)
                if key in existing:
                    continue
                SharingAuthorityFence.objects.create(
                    deployment_id=command.deployment_id,
                    authority_owner=reference.owner,
                    authority_reference=reference.reference,
                    authority_revision=1,
                    state=command.state.value,
                )
                changed += 1
    return changed


def _refresh_authority_fences(
    *, deployment_id: UUID, authority_refs: tuple[OwnedReference, ...]
) -> dict[tuple[str, str], int]:
    """Make freshly resolved owner facts allowed and return their checked revisions."""
    from engine.models import SharingAuthorityFence

    revisions: dict[tuple[str, str], int] = {}
    for reference in sorted(authority_refs, key=lambda item: (item.owner, item.reference)):
        key = (reference.owner, reference.reference)
        if key in revisions:
            continue
        fence = (
            SharingAuthorityFence.objects.select_for_update()
            .filter(
                deployment_id=deployment_id,
                authority_owner=reference.owner,
                authority_reference=reference.reference,
            )
            .first()
        )
        if fence is None:
            fence = SharingAuthorityFence.objects.create(
                deployment_id=deployment_id,
                authority_owner=reference.owner,
                authority_reference=reference.reference,
                authority_revision=1,
                state=AuthorityState.ALLOWED.value,
            )
        elif fence.state != AuthorityState.ALLOWED.value:
            fence.authority_revision += 1
            fence.state = AuthorityState.ALLOWED.value
            fence.save(update_fields=["authority_revision", "state", "updated_at"])
        revisions[key] = fence.authority_revision
    return revisions


def project_selector_resolution(
    *,
    deployment_id: UUID,
    sharing_binding_id: str,
    publisher_identity: OwnedReference | dict,
    resolution: SelectorResolution | dict,
    observed_at: datetime,
    freshness_deadline: datetime,
) -> MembershipProjection:
    """Turn a locked owner resolution into the next Engine authority projection."""
    from engine.models import MembershipProjection

    publisher = _as_ref(publisher_identity)
    resolution = (
        resolution if isinstance(resolution, SelectorResolution) else SelectorResolution.model_validate(resolution)
    )
    all_refs = (
        resolution.selector_authority_refs
        + tuple(item.authority_ref for item in resolution.subject_authorities)
        + tuple(item.authority_ref for item in resolution.publisher_requirements)
        + tuple(item.authority_ref for item in resolution.spending_eligibilities)
    )
    with transaction.atomic():
        current = (
            MembershipProjection.objects.select_for_update()
            .filter(
                deployment_id=deployment_id,
                sharing_binding_id=sharing_binding_id,
                selector_digest=resolution.selector_digest,
            )
            .first()
        )
        revisions = _refresh_authority_fences(deployment_id=deployment_id, authority_refs=all_refs)
        membership_revision = 1 if current is None else current.membership_revision + 1
        evidence = SharingAuthorityEvidence(
            contract_version="model-access-sharing-authority/v1",
            deployment_id=deployment_id,
            sharing_binding_id=sharing_binding_id,
            selector_digest=resolution.selector_digest,
            membership_revision=membership_revision,
            assessment_count=resolution.assessment_count,
            selector_authorities=tuple(
                SelectorAuthorityEvidence(
                    authority_ref=reference,
                    authority_revision=revisions[(reference.owner, reference.reference)],
                    state=AuthorityState.ALLOWED,
                )
                for reference in resolution.selector_authority_refs
            ),
            state=AuthorityState.ALLOWED,
            member_refs=resolution.member_refs,
            subject_authorizations=tuple(
                SubjectAuthorizationEvidence(
                    subject_ref=item.subject_ref,
                    authority_ref=item.authority_ref,
                    authority_revision=revisions[(item.authority_ref.owner, item.authority_ref.reference)],
                    state=AuthorityState.ALLOWED,
                )
                for item in resolution.subject_authorities
            ),
            publisher_authorities=tuple(
                PublisherAuthorityEvidence(
                    publisher_ref=publisher,
                    authority_ref=item.authority_ref,
                    authority_revision=revisions[(item.authority_ref.owner, item.authority_ref.reference)],
                    selector_digest=item.selector_digest,
                    scope=item.scope,
                    state=AuthorityState.ALLOWED,
                )
                for item in resolution.publisher_requirements
            ),
            spending_eligibilities=tuple(
                SpendingEligibilityEvidence(
                    authority_ref=item.authority_ref,
                    eligibility_revision=revisions[(item.authority_ref.owner, item.authority_ref.reference)],
                    basis=item.basis,
                    state=AuthorityState.ALLOWED,
                )
                for item in resolution.spending_eligibilities
            ),
            observed_at=observed_at,
            freshness_deadline=freshness_deadline,
        )
        return publish_membership_projection(evidence=evidence)


def publish_membership_projection(
    *,
    evidence: SharingAuthorityEvidence | dict,
) -> MembershipProjection:
    """Write one canonical, monotonic membership and authority projection.

    Keyed by ``selector_digest`` so evidence for a new selector version is a
    distinct row and never clobbers the evidence the active definition resolves
    against, even when a subsequent publication fails.
    """
    from engine.models import MembershipProjection

    evidence = _as_evidence(evidence)
    evidence_digest = compute_digest(evidence)
    payload = evidence.model_dump(mode="json")
    with transaction.atomic():
        projection = (
            MembershipProjection.objects.select_for_update()
            .filter(
                deployment_id=evidence.deployment_id,
                sharing_binding_id=evidence.sharing_binding_id,
                selector_digest=evidence.selector_digest,
            )
            .first()
        )
        _require_projection_fences(evidence)
        if projection is not None and evidence.membership_revision < projection.membership_revision:
            raise SharingError("sharing.membership_revision_regressed")
        if projection is not None and evidence.membership_revision == projection.membership_revision:
            if projection.evidence_digest != evidence_digest:
                raise SharingError("sharing.membership_revision_conflict")
            return projection

        values = {
            "membership_revision": evidence.membership_revision,
            "assessment_count": evidence.assessment_count,
            "state": evidence.state.value,
            "member_refs": payload["member_refs"],
            "selector_authorities": payload["selector_authorities"],
            "evidence_digest": evidence_digest,
            "subject_authorizations": payload["subject_authorizations"],
            "publisher_authorities": payload["publisher_authorities"],
            "spending_eligibilities": payload["spending_eligibilities"],
            "observed_at": evidence.observed_at,
            "freshness_deadline": evidence.freshness_deadline,
        }
        if projection is None:
            projection = MembershipProjection.objects.create(
                deployment_id=evidence.deployment_id,
                sharing_binding_id=evidence.sharing_binding_id,
                selector_digest=evidence.selector_digest,
                **values,
            )
        else:
            for field_name, value in values.items():
                setattr(projection, field_name, value)
            projection.save(update_fields=[*values, "updated_at"])
        _audit(
            "sharing_membership",
            entity_id=projection.pk,
            context=(
                f"binding={evidence.sharing_binding_id} membership_revision={evidence.membership_revision} "
                f"state={evidence.state.value} assessed={evidence.assessment_count} "
                f"members={len(evidence.member_refs)}"
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
