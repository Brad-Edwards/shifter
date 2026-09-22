"""CTF-owned event ancestry projection for credential authority checks."""

from uuid import UUID

from ctf.models import CTFEvent
from shared.identity_scope import ResourceScope
from workspaces.services import resource_scope_from_ids


def event_credential_scope(event_uuid: UUID) -> ResourceScope:
    """Return validated SQL ancestry, never an absent-scope installation default."""
    event = CTFEvent.objects.filter(pk=event_uuid, deleted_at__isnull=True).first()
    if event is None:
        raise ValueError("Credential scope unavailable")
    return resource_scope_from_ids(
        kind=event.scope_kind,
        account_id=event.account_id,
        organization_id=event.organization_id,
        workspace_id=event.workspace_id,
    )
