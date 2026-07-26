"""Behavior tests for the workspaces service facade (ADR-046, issue #1325).

Drives the real service against real rows. The authorization tests exist to go
red if the enforcement is removed: each one asks the seam a question a caller
would ask and asserts the *effect* (allowed / denied), not that a helper was
called.
"""

import dataclasses

import pytest
from django.contrib.auth import get_user_model

from workspaces import services
from workspaces.models import Organization, Workspace, WorkspaceMembership
from workspaces.roles import WorkspaceOperation, WorkspaceRole

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(suffix="a"):
    return User.objects.create_user(username=f"wss-{suffix}@e.com", email=f"wss-{suffix}@e.com")


# ---------------------------------------------------------------------------
# resolve_personal_workspace
# ---------------------------------------------------------------------------


def test_resolve_personal_workspace_creates_organization_workspace_and_owner_membership():
    user = _user()

    result = services.resolve_personal_workspace(user)

    workspace = Workspace.objects.get(personal_for_user=user)
    assert result.workspace_id == workspace.pk
    assert result.workspace_uuid == workspace.uuid
    assert result.organization_id == workspace.organization_id
    assert result.role == WorkspaceRole.OWNER.value
    assert WorkspaceMembership.objects.filter(workspace=workspace, user=user, role=WorkspaceRole.OWNER.value).exists()


def test_resolve_personal_workspace_is_idempotent():
    user = _user()

    first = services.resolve_personal_workspace(user)
    second = services.resolve_personal_workspace(user)

    assert first == second
    assert Workspace.objects.filter(personal_for_user=user).count() == 1
    assert Organization.objects.count() == 1
    assert WorkspaceMembership.objects.filter(user=user).count() == 1


def test_each_user_gets_a_distinct_personal_organization_not_a_shared_default():
    first_user = _user("one")
    second_user = _user("two")

    first = services.resolve_personal_workspace(first_user)
    second = services.resolve_personal_workspace(second_user)

    assert first.workspace_id != second.workspace_id
    # ADR-046-R4: the compatibility default is per user. A shared deployment-wide
    # organization would make every install single-tenant by construction.
    assert first.organization_id != second.organization_id
    assert Organization.objects.count() == 2


def test_authorization_result_is_immutable_and_carries_no_orm_model():
    user = _user()

    result = services.resolve_personal_workspace(user)

    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.workspace_id = 999
    assert not any(isinstance(value, (Workspace, Organization)) for value in dataclasses.astuple(result))


# ---------------------------------------------------------------------------
# authorize_workspace (public UUID)
# ---------------------------------------------------------------------------


def test_member_is_authorized_for_a_permitted_operation():
    user = _user()
    personal = services.resolve_personal_workspace(user)

    result = services.authorize_workspace(user, personal.workspace_uuid, WorkspaceOperation.LAUNCH_RANGE)

    assert result.workspace_id == personal.workspace_id
    assert result.role == WorkspaceRole.OWNER.value


def test_non_member_is_denied():
    owner = _user("owner")
    outsider = _user("outsider")
    personal = services.resolve_personal_workspace(owner)

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_workspace(outsider, personal.workspace_uuid, WorkspaceOperation.LAUNCH_RANGE)


def test_unknown_workspace_is_denied_the_same_way_a_non_member_is():
    """A denial must not disclose whether the workspace exists."""
    outsider = _user("outsider")
    owner = _user("owner")
    personal = services.resolve_personal_workspace(owner)
    unknown_uuid = "00000000-0000-4000-8000-000000000000"

    with pytest.raises(services.WorkspaceAuthorizationError) as unknown:
        services.authorize_workspace(outsider, unknown_uuid, WorkspaceOperation.LAUNCH_RANGE)
    with pytest.raises(services.WorkspaceAuthorizationError) as not_a_member:
        services.authorize_workspace(outsider, personal.workspace_uuid, WorkspaceOperation.LAUNCH_RANGE)

    assert str(unknown.value) == str(not_a_member.value)


def test_unknown_operation_is_denied_fail_closed():
    user = _user()
    personal = services.resolve_personal_workspace(user)

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_workspace(user, personal.workspace_uuid, "delete_everything")


def test_a_malformed_workspace_uuid_is_denied_rather_than_raising_a_value_error():
    user = _user()

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_workspace(user, "not-a-uuid", WorkspaceOperation.LAUNCH_RANGE)


def test_membership_in_one_workspace_does_not_authorize_another():
    user = _user("member")
    other_owner = _user("other")
    services.resolve_personal_workspace(user)
    other = services.resolve_personal_workspace(other_owner)

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_workspace(user, other.workspace_uuid, WorkspaceOperation.LAUNCH_RANGE)


# ---------------------------------------------------------------------------
# authorize_bound_workspace (trusted, already-persisted internal id)
# ---------------------------------------------------------------------------


def test_bound_workspace_authorization_accepts_a_member():
    user = _user()
    personal = services.resolve_personal_workspace(user)

    result = services.authorize_bound_workspace(user, personal.workspace_id, WorkspaceOperation.REASSIGN_RANGE)

    assert result.workspace_uuid == personal.workspace_uuid


def test_bound_workspace_authorization_denies_a_non_member():
    owner = _user("owner")
    outsider = _user("outsider")
    personal = services.resolve_personal_workspace(owner)

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_bound_workspace(outsider, personal.workspace_id, WorkspaceOperation.REASSIGN_RANGE)


def test_bound_workspace_authorization_denies_an_unknown_binding():
    user = _user()

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_bound_workspace(user, 987654, WorkspaceOperation.REASSIGN_RANGE)


def test_bound_workspace_authorization_denies_a_null_binding():
    """A range with no persisted workspace binding is not implicitly in scope."""
    user = _user()

    with pytest.raises(services.WorkspaceAuthorizationError):
        services.authorize_bound_workspace(user, None, WorkspaceOperation.REASSIGN_RANGE)
