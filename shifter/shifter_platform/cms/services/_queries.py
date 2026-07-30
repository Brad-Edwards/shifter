"""Read-only range/instance queries for system and participant workflows."""

from __future__ import annotations

from uuid import UUID

from cms.exceptions import CMSError
from cms.models import RangeInstance


def get_range_status_by_id(range_instance_id: int) -> str:
    """Get the current status of a RangeInstance by its PK.

    Returns:
        Status string, or ``"unknown"`` if not found.
    """
    try:
        # all_objects: status lookups must see soft-deleted (terminal/destroyed)
        # ranges so callers can report the final lifecycle state of a torn-down range.
        return str(RangeInstance.all_objects.values_list("status", flat=True).get(pk=range_instance_id))
    except RangeInstance.DoesNotExist:
        return "unknown"


def get_range_spec_by_id(range_instance_id: int) -> dict | None:
    """Get the range_spec dict from a RangeInstance by its PK.

    Returns:
        The range_spec dict, or ``None`` if not found.
    """
    try:
        # all_objects: range_spec lookups must see soft-deleted (terminal)
        # ranges so callers can correlate audit events to a torn-down range.
        spec = RangeInstance.all_objects.values_list("range_spec", flat=True).get(pk=range_instance_id)
        return spec if spec is None or isinstance(spec, dict) else None
    except RangeInstance.DoesNotExist:
        return None


def find_range_instance_id_by_request(request_id: str | UUID) -> int | None:
    """Find a RangeInstance PK by its provisioning request ID.

    Returns:
        The RangeInstance PK, or ``None`` if not found.
    """
    # all_objects: callback correlation needs to find ranges by request even
    # after the range has reached a terminal soft-deleted state.
    pk = (
        RangeInstance.all_objects.filter(
            request__request_id=request_id,
        )
        .values_list("pk", flat=True)
        .first()
    )
    return int(pk) if pk is not None else None


def get_range_target_instances(user) -> list[dict[str, str]]:
    """Get the accessible provisioned instances for a user's ready range.

    Explicit participant-access channels are authoritative when present. For
    example, POLARIS declares RDP/SSH access to Kali only even though the range
    also contains a DC target. Legacy rows that predate channel metadata keep
    the previous heuristic: show non-attacker targets, or fall back to attacker
    seats for single-workstation labs.

    Args:
        user: User whose participant-accessible instances are requested.

    Returns:
        List of dicts with name, private_ip, os_type for each accessible instance.
    """
    from engine.services import get_user_ready_range_instances
    from shared.enums import RangeSource, ResourceStatus
    from workspaces.services import WorkspaceOperation

    from ._range_workspace import authorize_range_workspace

    user_id = getattr(user, "id", None)
    if user_id is None:
        return []
    cms_range = (
        RangeInstance.objects.filter(
            user_id=user_id,
            range_source=RangeSource.CTF.value,
            status=ResourceStatus.READY.value,
            request__isnull=False,
        )
        .select_related("request")
        .order_by("-created_at")
        .first()
    )
    if cms_range is None:
        return []
    try:
        authorize_range_workspace(user, cms_range.workspace_id, WorkspaceOperation.ACCESS_RANGE)
    except CMSError:
        return []

    instances = list(
        get_user_ready_range_instances(
            user_id,
            request_id=cms_range.request.request_id,
            workspace_id=cms_range.workspace_id,
        )
    )
    declared_targets = [inst for inst in instances if _has_participant_access_channel(inst)]
    if declared_targets:
        return declared_targets
    # Current AWS state explicitly records an open participant-access binding
    # as ``None``. In attacker-workstation scenarios such as POLARIS, expose
    # that seat rather than the DC the participant attacks over the network.
    # Legacy rows omit the key entirely and retain the non-attacker heuristic.
    aws_attacker_seats = [inst for inst in instances if _is_aws_open_access_attacker(inst)]
    if aws_attacker_seats:
        return aws_attacker_seats
    targets = [inst for inst in instances if inst.get("role") != "attacker"]
    return targets if targets else instances


def _has_participant_access_channel(instance: dict[str, object]) -> bool:
    """Return whether a provisioned instance has an explicit user access channel."""
    channels = instance.get("participant_access_channels")
    if not isinstance(channels, list | tuple | set):
        return False
    return any(isinstance(channel, str) and channel.strip() for channel in channels)


def _is_aws_open_access_attacker(instance: dict[str, object]) -> bool:
    """Return whether current AWS state exposes an attacker seat without a closed binding."""
    return (
        instance.get("cloud_provider") == "aws"
        and "participant_access_channels" in instance
        and instance["participant_access_channels"] is None
        and instance.get("role") == "attacker"
    )
