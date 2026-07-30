"""Workspace-authorized facades for interactive range instance access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError

from cms.exceptions import CMSError
from cms.models import Instance
from workspaces.services import WorkspaceOperation

from ._range_workspace import authorize_range_workspace

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def _authorize_instance_access(user: User, instance_uuid: str) -> None:
    """Enforce ownership and the persisted request workspace binding."""
    try:
        instance = (
            Instance.objects.select_related("request")
            .filter(pk=instance_uuid, request__user_id=getattr(user, "id", None))
            .first()
        )
    except (ValidationError, ValueError) as exc:
        raise ValueError("Instance not found") from exc
    if instance is None:
        raise ValueError("Instance not found")
    try:
        authorize_range_workspace(user, instance.request.workspace_id, WorkspaceOperation.ACCESS_RANGE)
    except CMSError as exc:
        raise PermissionError("Instance not found") from exc


def connect_range_terminal(user: User, instance_uuid: str) -> Any:
    """Open an Engine terminal only after workspace authorization succeeds."""
    from engine.services import connect_terminal

    _authorize_instance_access(user, instance_uuid)
    return connect_terminal(user, instance_uuid)


def get_range_rdp_connection_info(user: User, instance_uuid: str) -> dict[str, Any]:
    """Resolve RDP data only after workspace authorization succeeds."""
    from engine.services import get_rdp_connection_info

    _authorize_instance_access(user, instance_uuid)
    return get_rdp_connection_info(user, instance_uuid)


def get_range_ssh_connection_info(user: User, instance_uuid: str) -> dict[str, Any]:
    """Resolve SSH data only after workspace authorization succeeds."""
    from engine.services import get_ssh_connection_info

    _authorize_instance_access(user, instance_uuid)
    return get_ssh_connection_info(user, instance_uuid)
