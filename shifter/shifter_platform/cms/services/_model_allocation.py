"""Resolve CMS-owned model launch facts before the atomic Engine outbox write."""

from uuid import UUID

from django.conf import settings
from django.db import transaction

from cms.models import RangeInstance
from cms.scenarios.model_needs import project_scenario_model_needs
from shared.model_access import ContractError, EventModelDemand, OwnedReference
from shared.model_access.reservation import ModelLaunchScope, WarmPreparationAuthority


def needs_model_preparation(request_id) -> bool:
    """Route lifecycle through the durable snapshot, not a mutable overlay alone."""
    from engine.services import get_model_launch_preparation

    if get_model_launch_preparation(request_id) is not None:
        return True
    instance = RangeInstance.objects.filter(request__request_id=request_id).first()
    if instance is None:
        return False
    projection = project_scenario_model_needs(instance.scenario_id, expected_digest=instance.model_package_digest)
    if projection.resolution_failed:
        raise ContractError("allocation.scenario_unavailable")
    return bool(projection.needs)


def _snapshot_needs(instance):
    """One package-bound identity controls both lifecycle routing and preparation."""
    from engine.services import get_model_launch_preparation

    prepared = get_model_launch_preparation(instance.request.request_id)
    if prepared is not None:
        if any(need.scenario_digest != instance.model_package_digest for need in prepared.needs):
            raise ContractError("allocation.scenario_unavailable")
        return prepared.needs
    projection = project_scenario_model_needs(instance.scenario_id, expected_digest=instance.model_package_digest)
    if projection.resolution_failed or (projection.needs and not projection.digest_verified):
        raise ContractError("allocation.scenario_unavailable")
    return tuple(projection.needs.values())


def _standalone_scope(instance, range_id, needs, deployment_id):
    """A non-event commitment uses the authored envelope and real lease window."""
    from engine.services import get_model_warm_scope

    warm = (
        get_model_warm_scope(instance.request.request_id, deployment_id=deployment_id)
        if instance.expires_at is None
        else None
    )
    if instance.range_source == "ctf" or (instance.expires_at is None and warm is None):
        raise ContractError("allocation.scope_unavailable")
    return ModelLaunchScope(
        kind="warm" if warm else "standalone",
        scope_id=warm.scope_id if warm else instance.request.request_id,
        draw_key=warm.draw_key if warm else instance.request.request_id,
        subject_ref=OwnedReference(owner="deployment", reference=f"range:{range_id}"),
        window_start=warm.window_start if warm else instance.created_at,
        window_end=warm.window_end if warm else instance.expires_at,
        authority_revisions=warm.authority_revisions if warm else (),
        system_preparation=WarmPreparationAuthority(
            owner_ref=OwnedReference(owner="management", reference=f"user:{instance.user_id}"),
            generation_id=warm.generation_id,
        )
        if warm
        else None,
        demands=tuple(
            EventModelDemand(
                workload_role=need.workload_role,
                expected_concurrency=1,
                per_participant_requests=need.limits.max_requests_per_window,
                per_participant_input_tokens=need.limits.max_input_tokens,
                per_participant_output_tokens=need.limits.max_output_tokens,
                allowed_strategy=need.allowed_strategies[0],
            )
            for need in needs
        ),
    )


def prepare_model_access_for_dispatch(request_id: UUID, *, range_id: UUID, renew: bool = False) -> None:
    """Authenticate current persisted ownership, then hand closed facts downward.

    Called after Range creation invalidates selectors and before enqueue, inside
    the same transaction. Missing policy, scope or authority denies required use.
    """
    from engine.services import get_model_launch_preparation, prepare_model_launch, project_model_launch_authority
    from management.services import resolve_model_access_users, resolve_model_preparation_user
    from workspaces.services import WorkspaceOperation, authorize_launch_workspace_locked

    with transaction.atomic():
        instance = RangeInstance.objects.select_related("request__user").get(request__request_id=request_id)
        prepared = get_model_launch_preparation(request_id)
        needs = _snapshot_needs(instance)
        if not needs:
            return
        if prepared is not None and prepared.unavailable_reason and not renew:
            # The first operation's optional absence is immutable. A later
            # lifecycle generation may explicitly renew against current policy.
            return
        owner = OwnedReference(owner="management", reference=f"user:{instance.user_id}")
        catalog = getattr(settings, "MODEL_ACCESS_CATALOG", None)
        if not getattr(settings, "MODEL_ACCESS_ENABLED", False) or catalog is None or not catalog.enabled:
            if any(need.required for need in needs):
                raise ContractError("allocation.policy_unavailable")
            from engine.services import disable_optional_model_preparation

            disable_optional_model_preparation(request_id=request_id, owner_ref=owner, needs=needs)
            return
        actor = instance.request.user
        scope = (
            ModelLaunchScope.model_validate(instance.model_launch_scope)
            if instance.model_launch_scope
            else _standalone_scope(instance, range_id, needs, catalog.deployment_id)
        )
        system = scope.system_preparation
        try:
            if system is None:
                resolve_model_access_users(actor, (instance.user_id,))
            elif (
                system.owner_ref != owner
                or actor.pk != instance.user_id
                or actor.is_active
                or actor.has_usable_password()
                or (system.kind == "warm_pool" and not actor.email.endswith("@warm-pool.invalid"))
            ):
                raise ContractError("allocation.system_owner_unavailable")
            else:
                resolve_model_preparation_user(instance.user_id)
            authorize_launch_workspace_locked(actor, instance.workspace_id, WorkspaceOperation.LAUNCH_RANGE)
        except Exception:
            raise ContractError("allocation.authority_unavailable") from None
        refs = (() if system else (owner,)) + (
            OwnedReference(owner="workspaces", reference=f"workspace-id:{instance.workspace_id}"),
            OwnedReference(owner="engine", reference=f"model-launch:{range_id}"),
        )
        from shared.model_access.projection_port import refresh_launch_projections

        refresh_launch_projections(catalog.deployment_id)
        revisions = project_model_launch_authority(deployment_id=catalog.deployment_id, authority_refs=refs)
        revisions += scope.authority_revisions
        prepare_model_launch(
            request_id=request_id,
            owner_ref=owner,
            needs=needs,
            scope=scope,
            authority_revisions=revisions,
            catalog=catalog,
            replace_revoked=renew,
        )


def resume_model_range(request_id: UUID) -> bool:
    """Join status transition, owner reauthorization and resumed launch atomically."""
    from engine.services import (
        dispatch_prepared_range_resume,
        get_authoritative_range_status,
        resolve_model_access_range_views,
        resume_range,
    )

    with transaction.atomic():
        if not resume_range(request_id, defer_dispatch=True):
            return False
        if get_authoritative_range_status(request_id=request_id) == "ready":
            return True
        (view,) = resolve_model_access_range_views(request_uuids=(request_id,))
        prepare_model_access_for_dispatch(request_id, range_id=view.range_uuid, renew=True)
        if not dispatch_prepared_range_resume(request_id):
            raise ContractError("allocation.dispatch_unavailable")
        return True
