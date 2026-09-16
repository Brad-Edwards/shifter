"""Authoritative allocation inputs, using the incumbent sharing projections.

Lock order: range owner, publication fence (shared), pools/bindings,
projections, complete ordered owner fences, affinity groups, real quotas.
Publication takes the fence exclusively; concurrent allocations share it.
"""

from django.db import connection
from django.utils import timezone

from engine.models import (
    AllocationGroup,
    MembershipProjection,
    SharingAuthorityFence,
    SharingBindingRecord,
)
from shared.model_access import BindingMatch, ContractError, OwnedReference, SharingBinding, compile_effective_policy
from shared.model_access.core_models import AssignmentAffinity, MembershipMode

from ._sharing_persistence import SharingError, _require_publisher_authority, _require_spending_eligibility
from ._sharing_resolution import _pinned_pool, _pinned_profile


def list_model_launch_refreshes(deployment_id):
    """Return stale definitions without mutating or resolving upstream authority."""
    from shared.model_access import AuthorityState, OwnedReference

    from ._sharing_persistence import _fence_matches

    result = []
    for record in SharingBindingRecord.objects.filter(deployment_id=deployment_id, state="active").order_by(
        "sharing_binding_id"
    ):
        revision = record.revisions.get(definition_revision=record.current_definition_revision)
        projection = MembershipProjection.objects.filter(
            deployment_id=deployment_id,
            sharing_binding_id=record.sharing_binding_id,
            selector_digest=revision.selector_digest,
        ).first()
        fresh = projection is not None and projection.is_fresh()
        if fresh:
            fresh = _fence_matches(
                deployment_id=deployment_id,
                authority_ref=OwnedReference(owner="engine", reference=f"membership:{projection.pk}"),
                revision=projection.membership_revision,
                state=AuthorityState.ALLOWED,
            )
        if fresh:
            for item in (
                projection.selector_authorities
                + projection.subject_authorizations
                + projection.publisher_authorities
                + projection.spending_eligibilities
            ):
                if (
                    not _fence_matches(
                        deployment_id=deployment_id,
                        authority_ref=OwnedReference.model_validate(item["authority_ref"]),
                        revision=item.get("authority_revision", item.get("eligibility_revision")),
                        state=AuthorityState.ALLOWED,
                    )
                    or item["state"] != "allowed"
                ):
                    fresh = False
                    break
        if not fresh:
            result.append(SharingBinding.model_validate(revision.definition))
    return tuple(result)


def _shared_rows(queryset):
    """Hold read locks until commit; independent admissions do not exclude each other."""
    if connection.vendor == "postgresql":
        sql, params = queryset.values_list("pk", flat=True).query.sql_with_params()
        with connection.cursor() as cursor:
            cursor.execute(sql + " FOR SHARE", params)
    return list(queryset)


def lock_policy_publication(deployment_id, *, writing=False):
    """Fence phantom binding publication without serializing independent readers."""
    row, _ = SharingAuthorityFence.objects.get_or_create(
        deployment_id=deployment_id,
        authority_owner="engine",
        authority_reference="model-policy-publication",
        defaults={"authority_revision": 1, "state": "allowed"},
    )
    if writing:
        row = SharingAuthorityFence.objects.select_for_update().get(pk=row.pk)
        from ._model_allocation_lifecycle import revoke_model_authorities

        revoke_model_authorities([row.pk])
        row.authority_revision += 1
        row.save(update_fields=["authority_revision", "updated_at"])
    elif connection.vendor == "postgresql":
        # Django has no FOR SHARE queryset operation. Reuse the shared-row
        # helper so SQL identifiers continue to come only from the ORM compiler.
        (row,) = _shared_rows(SharingAuthorityFence.objects.filter(pk=row.pk))
    return row


def _projections(deployment_id):
    """Read live binding revisions with the publication-compatible lock order."""
    records = list(
        SharingBindingRecord.objects.filter(
            deployment_id=deployment_id,
            state="active",
        ).order_by("sharing_binding_id")
    )
    result = []
    for record in records:
        revision = record.revisions.get(definition_revision=record.current_definition_revision)
        projections = _shared_rows(
            MembershipProjection.objects.filter(
                deployment_id=deployment_id,
                sharing_binding_id=record.sharing_binding_id,
                selector_digest=revision.selector_digest,
            )
        )
        projection = projections[0] if projections else None
        if projection is None or not projection.is_fresh():
            # Without complete current evidence absence cannot prove exclusion.
            raise ContractError("allocation.authority_unavailable")
        result.append((record, revision, projection))
    return result


def _expected_fences(request, rows):
    expected = {}
    for item in request.authority_revisions:
        ref = item.authority_ref
        expected[(ref.owner, ref.reference)] = item.authority_revision
    for _, revision, projection in rows:
        expected[("engine", f"membership:{projection.pk}")] = projection.membership_revision
        subject = request.subject_ref.model_dump(mode="json")
        values_to_check = [projection.selector_authorities]
        members = revision.frozen_members if revision.membership_mode == "snapshot" else projection.member_refs
        if subject in members:
            values_to_check.extend(
                [
                    [item for item in projection.subject_authorizations if item["subject_ref"] == subject],
                    projection.publisher_authorities,
                    projection.spending_eligibilities,
                ]
            )
        for values in values_to_check:
            for item in values:
                ref = item["authority_ref"]
                key = ref["owner"], ref["reference"]
                revision = item.get("authority_revision", item.get("eligibility_revision"))
                if item["state"] != "allowed" or (key in expected and expected[key] != revision):
                    raise ContractError("allocation.authority_unavailable")
                expected[key] = revision
    return expected


def locked_policy(request, catalog):
    """Prove membership or nonmembership, then call the one pure compiler."""
    publication = lock_policy_publication(request.deployment_id)
    rows = _projections(request.deployment_id)
    fences = [publication]
    for (owner, reference), revision in sorted(_expected_fences(request, rows).items()):
        locked_fences = _shared_rows(
            SharingAuthorityFence.objects.filter(
                deployment_id=request.deployment_id,
                authority_owner=owner,
                authority_reference=reference,
            )
        )
        fence = locked_fences[0] if locked_fences else None
        if fence is None or fence.state != "allowed" or fence.authority_revision != revision:
            raise ContractError("allocation.authority_unavailable")
        fences.append(fence)
    now = timezone.now()
    matches, revisions = [], []
    subject = request.subject_ref.model_dump(mode="json")
    for record, revision, projection in rows:
        binding = SharingBinding.model_validate(revision.definition)
        members = (
            revision.frozen_members if binding.membership_mode is MembershipMode.SNAPSHOT else projection.member_refs
        )
        if subject not in members:
            continue
        try:
            _require_publisher_authority(
                deployment_id=request.deployment_id,
                projection=projection,
                binding=binding,
                publisher=OwnedReference(owner=revision.publisher_owner, reference=revision.publisher_reference),
                lock=False,
            )
            _require_spending_eligibility(
                deployment_id=request.deployment_id,
                projection=projection,
                binding=binding,
                lock=False,
            )
        except SharingError as exc:
            raise ContractError("allocation.authority_unavailable") from exc
        if not any(
            item["subject_ref"] == subject and item["state"] == "allowed" for item in projection.subject_authorizations
        ):
            raise ContractError("allocation.authority_unavailable")
        matches.append(
            BindingMatch(
                binding=binding,
                pool=_pinned_pool(record.pool, revision.pool_routing_revision),
                profile=_pinned_profile(revision, binding),
                membership_revision=projection.membership_revision,
                membership_fresh=projection.is_fresh(now) and revision.catalog_digest == catalog.digest,
                matched_reason=binding.selector.kind.value,
            )
        )
        revisions.append(
            {
                "binding_id": record.sharing_binding_id,
                "definition_revision": revision.definition_revision,
                "membership_revision": projection.membership_revision,
                "fence_revision": projection.fence_revision,
                "selector_digest": projection.selector_digest,
                "pool_revision": revision.pool_routing_revision,
            }
        )
    policy = compile_effective_policy(
        deployment_id=request.deployment_id,
        catalog_digest=catalog.digest,
        evaluated_at=now,
        subject=request.subject_ref,
        matches=tuple(matches),
    )
    boundaries = [request.window_end]
    for _, revision, projection in rows:
        boundaries.append(projection.freshness_deadline)
        binding = SharingBinding.model_validate(revision.definition)
        boundaries.extend(
            boundary
            for boundary in (binding.effective_from, binding.effective_until)
            if boundary is not None and boundary > now
        )
    fresh_until = min(boundaries)
    return (
        policy,
        fences,
        {
            "publication_revision": publication.authority_revision,
            "bindings": revisions,
            "fresh_until": fresh_until.isoformat(),
        },
    )


def lock_assignment_groups(request, catalog, policy):
    """Serialize first shared assignment by its canonical affinity namespace."""
    routing = {item.logical_alias: item for item in policy.alias_routings}
    keys = {}
    for alias in catalog.aliases:
        if alias.profile_id != request.need.profile_id:
            continue
        selected = routing.get(alias.logical_alias)
        affinity = selected.affinity if selected is not None else alias.affinity
        if affinity is AssignmentAffinity.PER_RANGE:
            continue
        if selected is None:
            raise ContractError("allocation.shared_pool_required")
        owner = (
            f"{request.owner_ref.owner}:{request.owner_ref.reference}"
            if affinity is AssignmentAffinity.PER_USER
            else ""
        )
        keys[alias.logical_alias] = (selected.sharing_pool_id, selected.routing_revision, affinity.value, owner)
    groups = {}
    for alias, (pool_id, revision, affinity, owner) in sorted(keys.items(), key=lambda item: item[1]):
        row, _ = AllocationGroup.objects.get_or_create(
            deployment_id=request.deployment_id,
            sharing_pool_id=pool_id,
            routing_revision=revision,
            affinity=affinity,
            owner_ref=owner,
        )
        groups[alias] = AllocationGroup.objects.select_for_update().get(pk=row.pk)
    return groups
