"""SQL-authoritative event inventory for authorization delegation proofs."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from shared.authorization import TargetRef, bind_authorization_descendant_resolver


def list_authorization_event_uuids(workspace_ids: Sequence[int], limit: int) -> tuple[UUID, ...]:
    """Return at most ``limit`` event UUIDs for the exact workspace ids."""
    from ctf.models import CTFEvent

    if limit <= 0 or not workspace_ids:
        return ()
    return tuple(
        CTFEvent.objects.filter(workspace_id__in=workspace_ids).order_by("id").values_list("id", flat=True)[:limit]
    )


def _authorization_event_targets(workspace_ids: Sequence[int], limit: int) -> tuple[TargetRef, ...]:
    """Project SQL-owned event identities into neutral authorization targets."""
    return tuple(TargetRef("event", item) for item in list_authorization_event_uuids(workspace_ids, limit))


def register_authorization_event_inventory() -> None:
    """Bind the CTF-owned SQL event inventory to the neutral port."""
    bind_authorization_descendant_resolver("ctf.events", _authorization_event_targets)
