"""CTF event workspace authority for participant-owned range launches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ctf.exceptions import CTFRangeError

if TYPE_CHECKING:
    from ctf.models import CTFEvent


def event_launch_workspace_uuid(event: CTFEvent) -> str:
    """Return the event owner's authorized immutable launch workspace.

    The participant owns the resulting range but is intentionally not made a
    member of the organizer's tenant workspace.  The event's creation-time
    workspace binding is therefore reauthorized for its owner at launch.
    """
    from workspaces.services import WorkspaceAuthorizationError, WorkspaceOperation, authorize_bound_workspace

    try:
        authorization = authorize_bound_workspace(
            event.created_by,
            event.workspace_id,
            WorkspaceOperation.LAUNCH_RANGE,
        )
    except WorkspaceAuthorizationError as exc:
        raise CTFRangeError("The event workspace is unavailable for range launch") from exc
    return str(authorization.workspace_uuid)
