"""Workspace scope resolution for the CMS launch boundary (#1325, ADR-046-R3).

One place decides which workspace a launch belongs to. Both launch paths -- the
cyberscript ``create_range`` and the RAES-native ``create_raes_native_range`` --
call this, so scope is never resolved in a view, a serializer, or the
provisioner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cms.exceptions import CMSError
from workspaces.services import WorkspaceOperation

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def resolve_launch_workspace(user: User) -> int:
    """Resolve the workspace scope a launch by ``user`` belongs to.

    #1325 resolves the launcher's personal workspace, which preserves current
    single-user behavior exactly while giving every new range a real tenancy
    binding. #1327 replaces this with workspace selection and admission; keeping
    the decision here means that change lands in one function rather than at
    every call site.
    """
    from workspaces.services import authorize_bound_workspace, resolve_personal_workspace

    workspace_id = resolve_personal_workspace(user).workspace_id
    authorize_bound_workspace(user, workspace_id, WorkspaceOperation.LAUNCH_RANGE)
    return workspace_id


def authorize_range_workspace(
    user: User,
    workspace_id: int | None,
    operation: WorkspaceOperation,
) -> None:
    """Authorize an interactive operation against a persisted range binding."""
    from workspaces.services import WorkspaceAuthorizationError, authorize_bound_workspace

    try:
        authorize_bound_workspace(user, workspace_id, operation)
    except WorkspaceAuthorizationError as exc:
        raise CMSError("Range not found") from exc


def authorized_range_workspace_ids(
    user: User,
    operation: WorkspaceOperation,
) -> tuple[int, ...]:
    """Return workspace bindings visible to ``user`` for a collection query."""
    from workspaces.services import authorized_workspace_ids

    return authorized_workspace_ids(user, operation)
