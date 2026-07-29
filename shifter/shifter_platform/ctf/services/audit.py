"""CTF live-repair and range-recovery audit helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from shared.audit import (
    AuditAction,
    AuditActorType,
    AuditEntityType,
    AuditEvent,
    audit_log,
)

if TYPE_CHECKING:
    from ctf.models import (
        CTFContentHydrationReceipt,
        CTFEvent,
        CTFParticipant,
        CTFRangeRecovery,
    )


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
            entity_type=AuditEntityType.CONFIG,
            entity_id=_entity_id_from_uuid(challenge_id),
            action=AuditAction.UPDATE,
            actor_type=AuditActorType.USER,
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


def audit_content_hydration(
    *,
    actor_id: int,
    event: CTFEvent,
    receipt: CTFContentHydrationReceipt,
    outcome: str,
) -> None:
    """Strictly record successful or idempotent native CTF content hydration."""
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.CONFIG,
            entity_id=_entity_id_from_uuid(event.pk),
            action=AuditAction.CREATE if outcome == "created" else AuditAction.UPDATE,
            actor_type=AuditActorType.USER,
            actor_id=actor_id,
            new_state={
                "ctf_content_hydration": outcome,
                "event_id": str(event.pk),
                "scenario_id": receipt.scenario_id,
                "declared_digest": receipt.declared_digest,
                "challenge_count": receipt.challenge_count,
                "flag_count": receipt.flag_count,
                "hint_count": receipt.hint_count,
                "prerequisite_count": receipt.prerequisite_count,
            },
            context="ctf_content_hydration",
        ),
        strict=True,
    )


def audit_content_hydration_drift(
    *,
    actor_id: int | None,
    receipt: CTFContentHydrationReceipt,
) -> None:
    """Strictly record the first authorized mutation of hydrated content."""
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.CONFIG,
            entity_id=_entity_id_from_uuid(receipt.event_id),
            action=AuditAction.UPDATE,
            actor_type=AuditActorType.USER if actor_id else AuditActorType.SYSTEM,
            actor_id=actor_id,
            previous_state={"ctf_content_state": "pristine"},
            new_state={
                "ctf_content_state": "drifted",
                "event_id": str(receipt.event_id),
                "scenario_id": receipt.scenario_id,
                "reason": receipt.drift_reason,
            },
            context="ctf_content_hydration_drift",
        ),
        strict=True,
    )


def audit_range_recovery(
    *,
    actor_id: int | None,
    recovery: CTFRangeRecovery,
    participant: CTFParticipant,
    previous_status: str,
) -> None:
    """Record a completed participant range-recovery operation (#1018).

    Takes the ``CTFRangeRecovery`` record and the participant rather than
    their individual fields (keeps the parameter count within the project's
    limit): ``entity_id``, the strategy, and the replacement reference are
    all read off ``recovery``, and the resulting status is read off
    ``participant`` (already updated by :func:`ctf.services.range.recovery._ensure_participant_repointed`
    by the time this runs). ``entity_id`` is the old (pre-recovery)
    ``RangeInstance.pk`` -- already a positive int, so no UUID-to-int mapping
    is needed here (unlike :func:`audit_live_flag_repair`). Participant/event
    identity and the replacement reference are carried as sanitized strings
    in ``new_state``. The old range is always destroyed (no
    disposition/forensics concept).
    """
    old_range_instance_id = recovery.old_range_instance_id
    new_state: dict[str, Any] = {
        "status": participant.range_status,
        "event_id": str(recovery.event_id),
        "participant_id": str(participant.pk),
        "strategy": recovery.strategy,
        "replacement_range_instance_id": recovery.replacement_range_instance_id,
        "replacement_request_id": (str(recovery.replacement_request_id) if recovery.replacement_request_id else None),
    }
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.RANGE,
            entity_id=old_range_instance_id,
            action=AuditAction.RECOVER,
            actor_type=AuditActorType.USER if actor_id else AuditActorType.SYSTEM,
            actor_id=actor_id,
            previous_state={"status": previous_status, "range_instance_id": old_range_instance_id},
            new_state=new_state,
            context="ctf_range_recovery",
        )
    )


def audit_spare_provisioning(
    *,
    actor_id: int | None,
    event_id: UUID,
    target_count: int,
    existing: int,
    created: int,
) -> None:
    """Record one event spare-pool top-up action (#1018).

    One row per ``provision_event_spares`` call, not one per range -- each
    individual CMS range creation is already audited by
    ``cms.services.create_range`` (``AuditAction.PROVISION``).
    """
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.RANGE,
            entity_id=_entity_id_from_uuid(event_id),
            action=AuditAction.SPARE_PROVISION,
            actor_type=AuditActorType.USER if actor_id else AuditActorType.SYSTEM,
            actor_id=actor_id,
            new_state={
                "event_id": str(event_id),
                "target_count": target_count,
                "existing": existing,
                "created": created,
            },
            context="ctf_spare_provisioning",
        )
    )


def audit_vpn_profile_download(
    *,
    actor_id: int,
    participant_id: UUID,
    range_instance_id: int,
    generation: UUID,
    profile_version: str,
) -> None:
    """Record profile delivery without credential, topology, or provider data."""
    from shared.credential_delivery import audit_openvpn_profile_download

    audit_openvpn_profile_download(
        actor_id=actor_id,
        participant_id=participant_id,
        range_instance_id=range_instance_id,
        generation=generation,
        profile_version=profile_version,
        product="ctf",
    )
