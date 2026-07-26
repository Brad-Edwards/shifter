"""Public service facade for the workspaces tenancy domain (ADR-046-R1).

This module is the *only* surface other layers may import. They must not import
``workspaces.models`` and must not hold a cross-layer ForeignKey to a workspace;
they carry a validated scalar ``workspace_id`` instead (ADR-001-R2).

Membership mutation (invite, add, remove, change role) is deliberately absent:
#1325 establishes the data model and the per-user compatibility default, and
#1326 owns the membership lifecycle along with the last-owner invariant that
must guard it.
"""

from workspaces.roles import WorkspaceOperation

from ._authorization import (
    WorkspaceAuthorization,
    WorkspaceAuthorizationError,
    authorize_bound_workspace,
    authorize_workspace,
)
from ._personal import resolve_personal_workspace

# ``WorkspaceOperation`` is re-exported here on purpose: callers name the
# operation they want authorized, and the facade is the only module they may
# import (ADR-001-R1). The role vocabulary is NOT re-exported -- no other layer
# has business reading or comparing a role code.
__all__ = [
    "WorkspaceAuthorization",
    "WorkspaceAuthorizationError",
    "WorkspaceOperation",
    "authorize_bound_workspace",
    "authorize_workspace",
    "resolve_personal_workspace",
]
