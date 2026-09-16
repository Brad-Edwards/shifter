"""Import-safe public model admission bridges (Engine loads before app registry)."""


def fence_model_policy_publication(deployment_id):
    """Take the publication writer lock after owner resolution, before projection.

    The composition caller must keep its owning transaction open through binding
    publication; the binding service then reuses this lock, never inverts it.
    """
    from django.db import connection

    from shared.model_access import ContractError

    from ._model_allocation_authority import lock_policy_publication

    if not connection.in_atomic_block:
        raise ContractError("allocation.publication_transaction_required")
    return lock_policy_publication(deployment_id, writing=True)


def list_model_launch_refreshes(deployment_id):
    """Read the published definitions whose current owner evidence needs refreshing."""
    from ._model_allocation_authority import list_model_launch_refreshes as read

    return read(deployment_id)


def get_model_warm_scope(request_id, *, deployment_id):
    """Project the locked warm ledger through its owning Engine service."""
    from ._model_warm_authority import project_warm_scope

    return project_warm_scope(request_id, deployment_id=deployment_id)


def prepare_model_launch(*, request_id, owner_ref, needs, scope, authority_revisions, catalog, replace_revoked=False):
    """Persist closed downward launch inputs without dispatching or issuing access."""
    from ._model_allocation_launch import prepare_model_launch as prepare

    return prepare(
        request_id=request_id,
        owner_ref=owner_ref,
        needs=needs,
        scope=scope,
        authority_revisions=authority_revisions,
        catalog=catalog,
        replace_revoked=replace_revoked,
    )


def get_model_launch_preparation(request_id):
    """Read the immutable package needs that lifecycle refresh must preserve."""
    from engine.models import ModelLaunchPreparationRecord
    from shared.model_access.reservation import ModelLaunchPreparation

    from ._model_allocation import _validated

    row = ModelLaunchPreparationRecord.objects.filter(request_id=request_id).first()
    return _validated(ModelLaunchPreparation, row.intent) if row is not None else None


def disable_optional_model_preparation(*, request_id, owner_ref, needs):
    """Persist optional policy absence without erasing the original package needs."""
    from ._model_allocation_launch import disable_optional_model_preparation as disable

    return disable(request_id=request_id, owner_ref=owner_ref, needs=needs)


def project_model_launch_authority(*, deployment_id, authority_refs):
    """Project facts the caller checked under owning-service locks."""
    from ._model_allocation_launch import project_model_launch_authority as project

    return project(deployment_id=deployment_id, authority_refs=authority_refs)


def record_model_observations(catalog, observer):
    """Collect qualified observations outside transactions and persist their provenance."""
    from ._model_allocation_launch import record_model_observations as record

    return record(catalog, observer)
