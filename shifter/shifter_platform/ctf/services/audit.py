"""CTF live-repair audit helpers."""

from __future__ import annotations

from uuid import UUID

from risk_register.models import AuditLog
from risk_register.services import AuditEvent, audit_log


def _entity_id_from_uuid(entity_uuid: UUID) -> int:
    """Map a UUID to a positive int for AuditLog.entity_id."""
    return int.from_bytes(entity_uuid.bytes[:4], "big") % (2**31 - 1) or 1


def audit_live_flag_repair(
    *,
    actor_id: int,
    challenge_id: UUID,
    flag_id: UUID,
    event_id: UUID,
    action: str,
) -> None:
    """Record a live flag repair without storing flag plaintext or hashes."""
    audit_log(
        AuditEvent(
            entity_type=AuditLog.EntityType.CONFIG,
            entity_id=_entity_id_from_uuid(challenge_id),
            action=AuditLog.Action.UPDATE,
            actor_type=AuditLog.ActorType.USER,
            actor_id=actor_id,
            new_state={
                "ctf_live_flag_repair": action,
                "challenge_id": str(challenge_id),
                "flag_id": str(flag_id),
                "event_id": str(event_id),
            },
            context="ctf_live_flag_repair",
        )
    )
