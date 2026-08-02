"""Public service facade for the workspaces tenancy domain (ADR-046-R1/R8).

This module is the *only* surface other layers may import. They must not import
``workspaces.models`` and must not hold a cross-layer ForeignKey to a workspace;
they carry a validated scalar ``workspace_id`` instead (ADR-001-R2).

Membership mutation is exposed only through the transactional functions below;
callers never write tenancy models directly.
"""

from workspaces.roles import WorkspaceOperation

from ._authorization import (
    WorkspaceAuthorization,
    WorkspaceAuthorizationError,
    authorize_bound_workspace,
    authorize_launch_workspace_locked,
    authorize_workspace,
    authorized_workspace_ids,
)
from ._context import (
    ActorWorkspaceContext,
    OrganizationRef,
    list_actor_workspace_contexts,
)
from ._memberships import (
    MembershipAuditContext,
    WorkspaceMembershipError,
    WorkspaceMembershipProjection,
    add_workspace_member,
    change_workspace_member_role,
    get_self_membership,
    leave_workspace,
    list_workspace_memberships,
    remove_workspace_member,
)
from ._organization import (
    OrganizationAuditContext,
    OrganizationAuthorizationError,
    OrganizationProfile,
    OrganizationValidationError,
    get_organization_profile,
    list_administrable_organizations,
    update_organization_profile,
)
from ._personal import resolve_personal_workspace

# ``WorkspaceOperation`` is re-exported here on purpose: callers name the
# operation they want authorized, and the facade is the only module they may
# import (ADR-001-R1). The role vocabulary is NOT re-exported -- no other layer
# has business reading or comparing a role code.
__all__ = [
    "ActorWorkspaceContext",
    "MembershipAuditContext",
    "OrganizationAuditContext",
    "OrganizationAuthorizationError",
    "OrganizationProfile",
    "OrganizationRef",
    "OrganizationValidationError",
    "WorkspaceAuthorization",
    "WorkspaceAuthorizationError",
    "WorkspaceMembershipError",
    "WorkspaceMembershipProjection",
    "WorkspaceOperation",
    "add_workspace_member",
    "authorize_bound_workspace",
    "authorize_launch_workspace_locked",
    "authorize_workspace",
    "authorized_workspace_ids",
    "change_workspace_member_role",
    "get_organization_profile",
    "get_self_membership",
    "leave_workspace",
    "list_actor_workspace_contexts",
    "list_administrable_organizations",
    "list_workspace_memberships",
    "remove_workspace_member",
    "resolve_personal_workspace",
    "update_organization_profile",
]
