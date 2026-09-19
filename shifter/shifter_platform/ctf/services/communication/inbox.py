"""Live parent-scoped inbox policy and explicit participant receipt transitions."""

from django.db import transaction
from django.utils import timezone

from ctf.exceptions import CTFCommunicationError
from ctf.models import CTFEvent, CTFParticipant, ParticipantReceipt, RecipientSnapshot
from ctf.services.participant import viewing_participant_q
from ctf.services.participant.accounts import live_participant_for_user
from management.services import is_ctf_password_change_required, is_temporary_ctf_account


def _deny():
    raise CTFCommunicationError("Unavailable", code="CTF_COMMUNICATION_PARTICIPANT_DENIED")


def participant_for_inbox(user, event_id):
    """Receipt interaction grants no play authority; disqualified readers remain eligible."""
    if user is None or not user.is_active or getattr(getattr(user, "profile", None), "deleted_at", None):
        _deny()
    if is_temporary_ctf_account(user):
        live = live_participant_for_user(user)
        if live is None or live.event_id != event_id or is_ctf_password_change_required(user):
            _deny()
    participant = CTFParticipant.objects.filter(
        viewing_participant_q(), user_id=user.pk, event_id=event_id, event__deleted_at__isnull=True
    ).first()
    if participant is None:
        _deny()
    return participant


def inbox_for_participant(user, event_id):
    """Only committed in-app availability for this exact participation is visible."""
    participant = participant_for_inbox(user, event_id)
    return (
        RecipientSnapshot.objects.filter(
            event_id=event_id,
            participant_public_id=participant.pk,
            user_id=user.pk,
            receipt__isnull=False,
            intent__released_at__isnull=False,
        )
        .select_related("intent__revision", "receipt")
        .order_by("-intent__released_at", "-pk")
    )


def interact_with_receipt(user, event_id, snapshot_id, *, acknowledge=False):
    """Serialize live admission with event/participant removal and keep timestamps stable."""
    with transaction.atomic():
        if not CTFEvent.objects.select_for_update().filter(pk=event_id).exists():
            _deny()
        participant = participant_for_inbox(user, event_id)
        CTFParticipant.objects.select_for_update().get(pk=participant.pk)
        snapshot = inbox_for_participant(user, event_id).filter(pk=snapshot_id).first()
        if snapshot is None:
            _deny()
        receipt = ParticipantReceipt.objects.select_for_update().get(snapshot=snapshot)
        if acknowledge and snapshot.intent.acknowledgement_policy != "explicit":
            raise CTFCommunicationError("Acknowledgement not required", code="CTF_COMMUNICATION_ACK_POLICY")
        field = "acknowledged_at" if acknowledge else "read_at"
        if getattr(receipt, field) is None:
            setattr(receipt, field, timezone.now())
            receipt.save(update_fields=[field, "updated_at"])
        snapshot.receipt = receipt
        return snapshot
