"""Enforcing model demand must preserve CTF identity and reject malformed intent."""

from datetime import timedelta
from uuid import uuid4

import pytest

from ctf.services.range.model_allocation import project_event_model_scope
from shared.model_access import ContractError, OwnedReference

from ..engine.services.test_model_admission import _catalog
from .test_model_demand_declaration import _demand

pytestmark = pytest.mark.django_db(transaction=True)


def test_event_scope_carries_server_window_draw_and_authority(ctf_event, settings):
    settings.MODEL_ACCESS_CATALOG = _catalog()
    ctf_event.model_demand = [_demand()]
    ctf_event.save(update_fields=["model_demand", "updated_at"])
    draw = uuid4()
    subject = OwnedReference(owner="ctf", reference=f"draw:{draw}")
    scope = project_event_model_scope(ctf_event, draw, subject)
    assert scope.scope_id == ctf_event.pk
    assert scope.draw_key == draw
    assert scope.subject_ref == subject
    assert scope.window_end == ctf_event.get_cleanup_time()
    assert scope.authority_revisions[0].authority_ref.reference == f"event:{ctf_event.pk}"


def test_invalid_demand_is_not_dropped_by_enforcing_path(ctf_event, settings):
    settings.MODEL_ACCESS_CATALOG = _catalog()
    ctf_event.model_demand = [_demand(), {"workload_role": "broken"}]
    ctf_event.save(update_fields=["model_demand", "updated_at"])
    with pytest.raises(ContractError, match=r"allocation\.event_demand_invalid"):
        project_event_model_scope(ctf_event, uuid4(), OwnedReference(owner="ctf", reference="draw:test"))


def test_inactive_spare_has_explicit_preparation_authority_and_revokes_on_consumption(
    ctf_event, django_user_model, settings
):
    from django.db import transaction

    from cms.services._model_allocation import prepare_model_access_for_dispatch
    from ctf.models import CTFSpareRange
    from engine.models import ModelLaunchPreparationRecord
    from engine.services import record_model_observations
    from engine.services._model_allocation_launch import allocate_launch_models

    from ..cms.test_model_allocation_dispatch import setup_launch
    from ..engine.services.test_model_allocation import allocation_inputs

    owner, request, instance = setup_launch(django_user_model, settings)
    owner.is_active = False
    owner.set_unusable_password()
    owner.save(update_fields=["is_active", "password"])
    ctf_event.model_demand = [_demand()]
    ctf_event.save(update_fields=["model_demand", "updated_at"])
    spare = CTFSpareRange.objects.create(event=ctf_event, owner_user=owner)
    subject = OwnedReference(owner="ctf", reference=f"draw:{spare.pk}")
    scope = project_event_model_scope(ctf_event, request.draw_key, subject, spare_id=spare.pk)
    instance.range_source = "ctf"
    instance.model_launch_scope = scope.model_dump(mode="json")
    instance.save(update_fields=["range_source", "model_launch_scope"])
    _, _, observations = allocation_inputs(django_user_model)
    record_model_observations(settings.MODEL_ACCESS_CATALOG, lambda _: observations)
    with transaction.atomic():
        prepare_model_access_for_dispatch(request.request_id, range_id=request.range_id)
        (allocation,) = allocate_launch_models(
            {"resource": "raes-range", "operation": "provision", "request_id": str(request.request_id)},
            request.operation_id,
        )
    assert allocation.grant.state == "pending"
    assert allocation.snapshot["request"]["preparation_authority"] == {"owner": "ctf", "reference": f"spare:{spare.pk}"}
    assert ModelLaunchPreparationRecord.objects.get().intent["scope"]["system_preparation"]["kind"] == "ctf_spare"
    owner.refresh_from_db()
    assert not owner.is_active and not owner.has_usable_password()
    spare.status = "consumed"
    spare.save(update_fields=["status", "updated_at"])
    allocation.grant.refresh_from_db()
    assert allocation.grant.state == "revoked"
    with pytest.raises(ContractError, match=r"allocation\.system_owner_unavailable"):
        project_event_model_scope(ctf_event, request.draw_key, subject, spare_id=spare.pk)


def test_system_preparation_rejects_spare_from_another_event(ctf_event, django_user_model, settings):
    from ctf.models import CTFSpareRange

    from .conftest import make_ctf_event

    settings.MODEL_ACCESS_CATALOG = _catalog()
    ctf_event.model_demand = [_demand()]
    ctf_event.save(update_fields=["model_demand", "updated_at"])
    owner = django_user_model.objects.create_user(username="other-event-spare@system.invalid")
    owner.is_active = False
    owner.set_unusable_password()
    owner.save(update_fields=["is_active", "password"])
    other_event = make_ctf_event(
        created_by_id=ctf_event.created_by_id,
        workspace_id=ctf_event.workspace_id,
        model_demand=[_demand()],
    )
    other_event.save()
    other_spare = CTFSpareRange.objects.create(event=other_event, owner_user=owner)
    with pytest.raises(ContractError, match=r"allocation\.system_owner_unavailable"):
        project_event_model_scope(
            ctf_event,
            uuid4(),
            OwnedReference(owner="ctf", reference="draw:test"),
            spare_id=other_spare.pk,
        )


@pytest.mark.parametrize(
    "field", ["model_demand", "event_start", "event_end", "range_spinup_minutes", "cleanup_delay_hours"]
)
@pytest.mark.parametrize("already_allocated", [False, True])
def test_event_fact_mutation_fences_admission_and_pending_grant(
    ctf_event, django_user_model, settings, field, already_allocated
):
    from ctf.services.event import update_event
    from engine.models import ModelAllocation, ModelPendingGrant

    from ..engine.services.test_model_allocation import allocate, allocation_inputs

    catalog, request, observations = allocation_inputs(django_user_model)
    settings.MODEL_ACCESS_CATALOG = catalog
    ctf_event.model_demand = [_demand()]
    ctf_event.save(update_fields=["model_demand", "updated_at"])
    scope = project_event_model_scope(ctf_event, request.draw_key, request.subject_ref)
    request = request.model_copy(
        update={
            "scope_kind": scope.kind,
            "scope_id": scope.scope_id,
            "window_start": scope.window_start,
            "window_end": scope.window_end,
            "demand": scope.demands[0],
            "authority_revisions": request.authority_revisions + scope.authority_revisions,
        }
    )
    inputs = catalog, request, observations
    allocation = allocate(inputs) if already_allocated else None
    old_value = getattr(ctf_event, field)
    if field == "model_demand":
        replacement = [{**_demand(), "expected_concurrency": 8}]
    elif field in {"event_start", "event_end"}:
        replacement = old_value + timedelta(minutes=1)
    else:
        replacement = old_value + 1

    # Exercise the organizer's real transaction after the projection lock has
    # been released, not a direct write to the Engine authority projection.
    update_event(ctf_event.pk, {field: replacement})
    if allocation is None:
        with pytest.raises(ContractError, match=r"allocation\.authority_unavailable"):
            allocate(inputs)
        assert not ModelAllocation.objects.exists()
        assert not ModelPendingGrant.objects.exists()
    else:
        allocation.grant.refresh_from_db()
        assert allocation.grant.state == "revoked"
        assert allocation.grant.grant_epoch == 2
