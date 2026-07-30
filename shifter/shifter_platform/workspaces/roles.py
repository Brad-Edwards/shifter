"""Closed workspace role and operation vocabularies (ADR-046-R2/R8)."""

from django.db import models


class WorkspaceRole(models.TextChoices):
    """Roles a user may hold in a workspace."""

    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class WorkspaceOperation(models.TextChoices):
    """Operations the workspace authorization seam can be asked about."""

    LAUNCH_RANGE = "launch_range", "Launch a range in the workspace"
    REASSIGN_RANGE = "reassign_range", "Reassign a range within the workspace"
    READ_RANGE = "read_range", "Read an owned range in the workspace"
    MANAGE_RANGE = "manage_range", "Manage an owned range in the workspace"
    ACCESS_RANGE = "access_range", "Access an owned range in the workspace"
    READ_SELF_MEMBERSHIP = "read_self_membership", "Read own workspace membership"
    READ_MEMBERS = "read_members", "Read the workspace membership roster"
    ADD_MEMBER = "add_member", "Add a workspace member"
    CHANGE_MEMBER_ROLE = "change_member_role", "Change a workspace member role"
    REMOVE_MEMBER = "remove_member", "Remove a workspace member"
    LEAVE_WORKSPACE = "leave_workspace", "Leave the workspace"


#: Role-to-operation policy. Callers must not re-derive permissions from a role
#: code themselves. Resource operations remain additive to the existing
#: per-range owner/source/lifecycle/access gates.
_RESOURCE_OPERATIONS = frozenset(
    {
        WorkspaceOperation.LAUNCH_RANGE.value,
        WorkspaceOperation.REASSIGN_RANGE.value,
        WorkspaceOperation.READ_RANGE.value,
        WorkspaceOperation.MANAGE_RANGE.value,
        WorkspaceOperation.ACCESS_RANGE.value,
        WorkspaceOperation.READ_SELF_MEMBERSHIP.value,
        WorkspaceOperation.LEAVE_WORKSPACE.value,
    }
)
_MEMBERSHIP_MANAGEMENT_OPERATIONS = frozenset(
    {
        WorkspaceOperation.READ_MEMBERS.value,
        WorkspaceOperation.ADD_MEMBER.value,
        WorkspaceOperation.CHANGE_MEMBER_ROLE.value,
        WorkspaceOperation.REMOVE_MEMBER.value,
    }
)

ROLE_OPERATIONS: dict[str, frozenset[str]] = {
    WorkspaceRole.OWNER.value: _RESOURCE_OPERATIONS | _MEMBERSHIP_MANAGEMENT_OPERATIONS,
    WorkspaceRole.ADMIN.value: _RESOURCE_OPERATIONS | _MEMBERSHIP_MANAGEMENT_OPERATIONS,
    WorkspaceRole.MEMBER.value: _RESOURCE_OPERATIONS,
}


def role_permits(role: str, operation: str) -> bool:
    """Return True when ``role`` is allowed to perform ``operation``.

    Unknown roles and unknown operations are denied: the policy is fail-closed,
    so a role code that predates a vocabulary change never silently gains
    authority.
    """
    return operation in ROLE_OPERATIONS.get(role, frozenset())
