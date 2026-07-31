"""Optional workspace selection, lock-safe reauth, and the launch-admission seam (#1327).

These drive the real ``cms.services.create_range`` facade. They prove ADR-046-R9
(optional public UUID selection, no silent fallback, lock-safe reauthorization at
reservation) and ADR-046-R10 (one pre-reservation workspace launch-admission seam
both create paths pass through). Binding/consistency of an already-scoped range is
covered by ``test_range_workspace_binding``; here the focus is *selection and
admission*.
"""

from __future__ import annotations

import pytest

from cms import services
from cms.exceptions import CMSError, WorkspaceLaunchDenied
from cms.models import RangeInstance
from cms.services import _range_create
from engine.models import Range as EngineRange
from shared.enums import RangeSource
from workspaces.models import Organization, Workspace, WorkspaceMembership
from workspaces.roles import WorkspaceRole
from workspaces.services import resolve_personal_workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="ws-select@e.com", email="ws-select@e.com")


def _shared_workspace(*members: object, role: WorkspaceRole = WorkspaceRole.MEMBER) -> Workspace:
    """A non-personal workspace whose given users hold ``role`` memberships."""
    organization = Organization.objects.create(name="Shared Org")
    workspace = Workspace.objects.create(organization=organization, name="Shared")
    for member in members:
        WorkspaceMembership.objects.create(workspace=workspace, user=member, role=role.value)
    return workspace


def _launch(user, make_agent, hydratable_scenario, **kwargs):
    services.create_range(user, hydratable_scenario.scenario_id, {"windows": make_agent(user).id}, **kwargs)
    return RangeInstance.objects.get(user_id=user.id)


def test_omitting_the_selection_binds_the_personal_workspace(user, make_agent, hydratable_scenario):
    range_instance = _launch(user, make_agent, hydratable_scenario)

    assert range_instance.workspace_id == resolve_personal_workspace(user).workspace_id


def test_selecting_a_member_workspace_binds_it_on_all_three_projections(user, make_agent, hydratable_scenario):
    workspace = _shared_workspace(user)

    range_instance = _launch(user, make_agent, hydratable_scenario, workspace_uuid=str(workspace.uuid))

    engine_range = EngineRange.objects.get(request__request_id=range_instance.request.request_id)
    assert range_instance.workspace_id == workspace.id
    assert range_instance.request.workspace_id == workspace.id
    assert engine_range.workspace_id == workspace.id


def test_selecting_a_non_member_workspace_is_denied_without_personal_fallback(
    user, django_user_model, make_agent, hydratable_scenario
):
    other = django_user_model.objects.create_user(username="ws-owner2@e.com", email="ws-owner2@e.com")
    workspace = _shared_workspace(other)  # user is deliberately not a member

    with pytest.raises(WorkspaceLaunchDenied):
        _launch(user, make_agent, hydratable_scenario, workspace_uuid=str(workspace.uuid))

    # A denied UUID must not silently fall back to the personal workspace.
    assert not RangeInstance.objects.filter(user_id=user.id).exists()


def test_selecting_a_malformed_workspace_uuid_is_denied(user, make_agent, hydratable_scenario):
    with pytest.raises(WorkspaceLaunchDenied):
        _launch(user, make_agent, hydratable_scenario, workspace_uuid="not-a-uuid")

    assert not RangeInstance.objects.filter(user_id=user.id).exists()


def test_selecting_a_workspace_after_membership_removal_is_denied(user, make_agent, hydratable_scenario):
    workspace = _shared_workspace(user)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()

    with pytest.raises(WorkspaceLaunchDenied):
        _launch(user, make_agent, hydratable_scenario, workspace_uuid=str(workspace.uuid))

    assert not RangeInstance.objects.filter(user_id=user.id).exists()


def test_reservation_reauthorizes_under_lock_even_when_first_pass_passed(
    user, make_agent, hydratable_scenario, monkeypatch
):
    """The locked reauthorization is a real second gate at reservation (ADR-046-R9).

    Simulate the TOCTOU the lock exists to close: force the first-pass resolution
    to hand back a scope the actor could reach a moment ago, then revoke the
    membership before reservation. The locked re-check must deny and no range row
    may be created.
    """
    workspace = _shared_workspace(user)
    monkeypatch.setattr(_range_create, "resolve_launch_workspace", lambda _user, _uuid=None: workspace.id)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()

    with pytest.raises(WorkspaceLaunchDenied):
        _launch(user, make_agent, hydratable_scenario, workspace_uuid=str(workspace.uuid))

    assert not RangeInstance.objects.filter(user_id=user.id).exists()


def test_launch_routes_through_the_workspace_admission_seam(user, make_agent, hydratable_scenario, monkeypatch):
    """Both the personal default and an explicit selection pass one admission call (ADR-046-R10)."""
    calls: list[dict[str, object]] = []
    original = _range_create.admit_workspace_launch

    def _spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(_range_create, "admit_workspace_launch", _spy)

    range_instance = _launch(user, make_agent, hydratable_scenario)

    assert len(calls) == 1
    assert calls[0]["workspace_id"] == range_instance.workspace_id
    assert calls[0]["range_source"] is RangeSource.MISSION_CONTROL
    assert calls[0]["user"] == user


def test_admission_denial_stops_the_launch_before_reservation(user, make_agent, hydratable_scenario, monkeypatch):
    def _deny(**_kwargs):
        raise CMSError("Workspace launch not admitted")

    monkeypatch.setattr(_range_create, "admit_workspace_launch", _deny)

    with pytest.raises(CMSError):
        _launch(user, make_agent, hydratable_scenario)

    assert not RangeInstance.objects.filter(user_id=user.id).exists()
