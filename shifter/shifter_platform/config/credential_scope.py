"""Compose domain-owned scalar ancestry without cross-domain model access."""

from cms.services import range_credential_scope
from ctf.services.credential_scope import event_credential_scope
from shared.authorization import TargetRef
from shared.identity_scope import ResourceScope
from workspaces.services import hierarchy_target_scope


def temporary_participant_credential(user):
    """Bind temporary proof to the exact CTF-owned live event lifecycle."""
    from ctf.services.participant.accounts import live_participant_for_user
    from shared.authorization import CredentialCeiling
    from shared.credentials import CredentialContext
    from shared.principal_port import principal_for_user

    participant = live_participant_for_user(user)
    if participant is None:
        raise ValueError("Temporary credential unavailable")
    return CredentialContext(
        principal=principal_for_user(user),
        kind="temporary",
        credential_uuid=participant.pk,
        ceiling=CredentialCeiling(
            frozenset({"event.read", "event.participate"}), TargetRef("event", participant.event_id)
        ),
        scopes=frozenset(),
        event_uuid=participant.event_id,
    )


def credential_target_scope(target: TargetRef) -> ResourceScope:
    """Resolve concrete target ancestry; never trust caller-supplied ancestry."""
    if target.type == "event":
        return event_credential_scope(target.uuid)
    if target.type == "range":
        return range_credential_scope(target.uuid)
    return hierarchy_target_scope(target.type, target.uuid)
