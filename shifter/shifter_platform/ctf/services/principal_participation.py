"""Principal-aware participation projection for the unified credential boundary."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ctf.enums import EVENT_TERMINAL_STATUSES
from ctf.models import CTFEvent, CTFParticipant
from ctf.services.credential_scope import event_credential_scope
from ctf.services.participant import eligible_participant_q
from shared.audit import AuditAction, AuditEntityType, AuditEvent, audit_log, principal_actor_fields
from shared.authorization import AuthorizationRequest, TargetRef, configured_authorization_provider
from shared.credentials import CredentialContext
from shared.identity_scope import PrincipalRef
from shared.principal_port import resolve_principal


def admit_service_participant(
    actor: CredentialContext, event_uuid: UUID, principal: PrincipalRef, *, name: str
) -> CTFParticipant:
    """Attach a service to the existing event participation lifecycle, not a User."""
    resolve_principal(actor.principal)
    resolve_principal(principal)
    if actor.kind == "temporary" or principal.kind != "service" or not name.strip() or len(name) > 100:
        raise ValueError("Participation denied")
    request = AuthorizationRequest(
        actor.principal,
        "event.manage_participants",
        TargetRef("event", event_uuid),
        event_credential_scope(event_uuid),
        actor.ceiling,
    )
    if not configured_authorization_provider().check(request).allowed:
        raise ValueError("Participation denied")
    with transaction.atomic():
        event = CTFEvent.objects.select_for_update().filter(pk=event_uuid).first()
        if event is None or event.status in EVENT_TERMINAL_STATUSES:
            raise ValueError("Participation denied")
        if CTFParticipant.objects.filter(event=event, principal_uuid=principal.uuid).exists():
            raise ValueError("Participation already exists")
        participant = CTFParticipant.objects.create(
            event=event, principal_uuid=principal.uuid, name=name.strip(), status="active", registered_at=timezone.now()
        )
        audit_log(
            AuditEvent(
                entity_type=AuditEntityType.PRINCIPAL,
                entity_id=0,
                entity_ref=str(principal.uuid),
                action=AuditAction.UPDATE,
                new_state={
                    "participant_uuid": str(participant.pk),
                    "event_uuid": str(event_uuid),
                },
                context="service_participation",
                **principal_actor_fields(actor.principal),
            ),
            strict=True,
        )
        return participant


def participant_for_credential(credential: CredentialContext, event_uuid: UUID) -> CTFParticipant:
    """Resolve exact participation under current proof, live policy and lifecycle."""
    resolve_principal(credential.principal)
    if not credential.ceiling.permits("event.participate", TargetRef("event", event_uuid)):
        raise ValueError("Participation denied")
    if credential.kind == "temporary":
        if credential.event_uuid != event_uuid:
            raise ValueError("Participation denied")
    else:
        request = AuthorizationRequest(
            credential.principal,
            "event.participate",
            TargetRef("event", event_uuid),
            event_credential_scope(event_uuid),
            credential.ceiling,
        )
        if not configured_authorization_provider().check(request).allowed:
            raise ValueError("Participation denied")
    now = timezone.now()
    participant = CTFParticipant.objects.filter(
        eligible_participant_q(),
        principal_uuid=credential.principal.uuid,
        event_id=event_uuid,
        event__status__in=("active", "paused"),
        event__event_start__lte=now,
        event__event_end__gt=now,
    ).first()
    if participant is None:
        raise ValueError("Participation denied")
    return participant
