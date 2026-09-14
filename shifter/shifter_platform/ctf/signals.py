"""CTF signal receivers for CMS events.

Connects to CMS signals to keep CTF data in sync with range status changes.
"""

from __future__ import annotations

import logging

from django.db import connection
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from cms.services import range_status_changed
from shared.enums import ResourceStatus
from shared.model_access import OwnedReference

logger = logging.getLogger(__name__)


def _authority_ref(noun: str, value) -> OwnedReference | None:
    if value is None:
        return None
    return OwnedReference(owner="ctf", reference=f"{noun}:{value}")


def _invalidate_model_access(references: list[OwnedReference | None], reason: str) -> None:
    from ctf.bridges import cms_invalidate_model_access_authority
    from shared.model_access import AuthorityInvalidation, AuthorityState

    unique = {(reference.owner, reference.reference): reference for reference in references if reference is not None}
    if not unique:
        return
    cms_invalidate_model_access_authority(
        AuthorityInvalidation(
            deployment_id=None,
            authority_refs=tuple(unique[key] for key in sorted(unique)),
            state=AuthorityState.UNKNOWN,
            reason=reason,
        )
    )


def _capture_authority_fields(sender, instance, fields: tuple[str, ...]) -> None:
    if instance._state.adding:
        instance._model_access_authority_before = None
        return
    instance._model_access_authority_before = sender.all_objects.filter(pk=instance.pk).values(*fields).first()


def _changed(instance, fields: tuple[str, ...]) -> bool:
    before = getattr(instance, "_model_access_authority_before", None)
    return before is None or any(before[field] != getattr(instance, field) for field in fields)


from ctf.models import (  # noqa: E402 - Django signal senders are imported after setup
    CTFCohort,
    CTFEvent,
    CTFEventStaff,
    CTFParticipant,
    CTFSpareRange,
    CTFTeam,
)

_PARTICIPANT_AUTHORITY_FIELDS = (
    "event_id",
    "team_id",
    "cohort_id",
    "range_instance_id",
    "status",
    "registered_at",
    "deleted_at",
)
_SPARE_AUTHORITY_FIELDS = (
    "event_id",
    "team_id",
    "cohort_id",
    "range_instance_id",
    "status",
    "deleted_at",
)


@receiver(pre_save, sender=CTFParticipant, dispatch_uid="ctf.model_access.participant.capture")
def capture_participant_model_access(sender, instance, **kwargs) -> None:
    _capture_authority_fields(sender, instance, _PARTICIPANT_AUTHORITY_FIELDS)


@receiver(post_save, sender=CTFParticipant, dispatch_uid="ctf.model_access.participant.invalidate")
def invalidate_participant_model_access(sender, instance, **kwargs) -> None:
    if not _changed(instance, _PARTICIPANT_AUTHORITY_FIELDS):
        return
    before = getattr(instance, "_model_access_authority_before", None) or {}
    _invalidate_model_access(
        [
            _authority_ref("participant", instance.pk),
            _authority_ref("event", before.get("event_id")),
            _authority_ref("event", instance.event_id),
            _authority_ref("team", before.get("team_id")),
            _authority_ref("team", instance.team_id),
            _authority_ref("cohort", before.get("cohort_id")),
            _authority_ref("cohort", instance.cohort_id),
        ],
        "ctf-participant-changed",
    )


@receiver(pre_save, sender=CTFSpareRange, dispatch_uid="ctf.model_access.spare.capture")
def capture_spare_model_access(sender, instance, **kwargs) -> None:
    _capture_authority_fields(sender, instance, _SPARE_AUTHORITY_FIELDS)


@receiver(post_save, sender=CTFSpareRange, dispatch_uid="ctf.model_access.spare.invalidate")
def invalidate_spare_model_access(sender, instance, **kwargs) -> None:
    if not _changed(instance, _SPARE_AUTHORITY_FIELDS):
        return
    before = getattr(instance, "_model_access_authority_before", None) or {}
    _invalidate_model_access(
        [
            _authority_ref("spare", instance.pk),
            _authority_ref("event", before.get("event_id")),
            _authority_ref("event", instance.event_id),
            _authority_ref("team", before.get("team_id")),
            _authority_ref("team", instance.team_id),
            _authority_ref("cohort", before.get("cohort_id")),
            _authority_ref("cohort", instance.cohort_id),
        ],
        "ctf-spare-changed",
    )


def _capture_container(sender, instance, **kwargs) -> None:
    _capture_authority_fields(sender, instance, ("event_id", "deleted_at"))


def _invalidate_container(noun: str, instance) -> None:
    if not _changed(instance, ("event_id", "deleted_at")):
        return
    before = getattr(instance, "_model_access_authority_before", None) or {}
    _invalidate_model_access(
        [
            _authority_ref(noun, instance.pk),
            _authority_ref("event", before.get("event_id")),
            _authority_ref("event", instance.event_id),
        ],
        f"ctf-{noun}-changed",
    )


@receiver(pre_save, sender=CTFTeam, dispatch_uid="ctf.model_access.team.capture")
@receiver(pre_save, sender=CTFCohort, dispatch_uid="ctf.model_access.cohort.capture")
def capture_model_access_container(sender, instance, **kwargs) -> None:
    _capture_container(sender, instance)


@receiver(post_save, sender=CTFTeam, dispatch_uid="ctf.model_access.team.invalidate")
@receiver(pre_delete, sender=CTFTeam, dispatch_uid="ctf.model_access.team.delete")
def invalidate_team_model_access(sender, instance, **kwargs) -> None:
    _invalidate_container("team", instance)


@receiver(post_save, sender=CTFCohort, dispatch_uid="ctf.model_access.cohort.invalidate")
@receiver(pre_delete, sender=CTFCohort, dispatch_uid="ctf.model_access.cohort.delete")
def invalidate_cohort_model_access(sender, instance, **kwargs) -> None:
    _invalidate_container("cohort", instance)


@receiver(pre_save, sender=CTFEvent, dispatch_uid="ctf.model_access.event.capture")
def capture_event_model_access(sender, instance, **kwargs) -> None:
    _capture_authority_fields(sender, instance, ("created_by_id", "status", "deleted_at"))


@receiver(post_save, sender=CTFEvent, dispatch_uid="ctf.model_access.event.invalidate")
@receiver(pre_delete, sender=CTFEvent, dispatch_uid="ctf.model_access.event.delete")
def invalidate_event_model_access(sender, instance, **kwargs) -> None:
    if _changed(instance, ("created_by_id", "status", "deleted_at")):
        _invalidate_model_access(
            [_authority_ref("event", instance.pk)],
            "ctf-event-changed",
        )


@receiver(post_save, sender=CTFEventStaff, dispatch_uid="ctf.model_access.staff.invalidate")
@receiver(pre_delete, sender=CTFEventStaff, dispatch_uid="ctf.model_access.staff.delete")
def invalidate_event_staff_model_access(sender, instance, **kwargs) -> None:
    if connection.in_atomic_block:
        tuple(CTFEvent.objects.select_for_update().filter(pk=instance.event_id))
    _invalidate_model_access(
        [_authority_ref("event", instance.event_id)],
        "ctf-event-staff-changed",
    )


@receiver(pre_save, sender=CTFEventStaff, dispatch_uid="ctf.model_access.staff.lock")
def lock_event_for_staff_model_access(sender, instance, **kwargs) -> None:
    if connection.in_atomic_block:
        tuple(CTFEvent.objects.select_for_update().filter(pk=instance.event_id))


@receiver(pre_delete, sender=CTFParticipant, dispatch_uid="ctf.model_access.participant.delete")
def invalidate_deleted_participant_model_access(sender, instance, **kwargs) -> None:
    _invalidate_model_access(
        [
            _authority_ref("participant", instance.pk),
            _authority_ref("event", instance.event_id),
            _authority_ref("team", instance.team_id),
            _authority_ref("cohort", instance.cohort_id),
        ],
        "ctf-participant-deleted",
    )


@receiver(pre_delete, sender=CTFSpareRange, dispatch_uid="ctf.model_access.spare.delete")
def invalidate_deleted_spare_model_access(sender, instance, **kwargs) -> None:
    _invalidate_model_access(
        [
            _authority_ref("spare", instance.pk),
            _authority_ref("event", instance.event_id),
            _authority_ref("team", instance.team_id),
            _authority_ref("cohort", instance.cohort_id),
        ],
        "ctf-spare-deleted",
    )


@receiver(range_status_changed)
def sync_ctf_participant_range_status(
    sender,
    range_instance_id: int,
    new_status: str,
    previous_status: str,
    **kwargs,
) -> None:
    """Update CTFParticipant.range_status when CMS reports a status change."""
    from ctf.models import CTFParticipant

    participants = CTFParticipant.objects.filter(
        range_instance_id=range_instance_id,
    )

    updated = 0
    for participant in participants:
        if new_status == ResourceStatus.DESTROYED.value:
            participant.range_instance_id = None
            participant.range_status = ""
            participant.save(update_fields=["range_instance_id", "range_status", "updated_at"])
            updated += 1
        elif participant.range_status != new_status:
            participant.range_status = new_status
            participant.save(update_fields=["range_status", "updated_at"])
            updated += 1

    if updated:
        logger.info(
            "Synced range_status=%s for %d CTF participant(s) (range_instance_id=%s, was=%s)",
            new_status,
            updated,
            range_instance_id,
            previous_status,
        )


@receiver(range_status_changed)
def sync_ctf_spare_range_status(
    sender: None,
    range_instance_id: int,
    new_status: str,
    previous_status: str,
    **kwargs,
) -> None:
    """Update CTFSpareRange.status when CMS reports a status change (#1018).

    This is the "existing event projection" that a spare's status uses to
    reach ``ready``/``failed`` -- no separate polling loop is introduced for
    the spare pool. Only unconsumed spares are touched: once a spare is
    consumed, its range belongs to the participant and further status
    changes are the participant-range projection's concern
    (:func:`sync_ctf_participant_range_status`), not the pool's.
    """
    from ctf.enums import SpareRangeStatus
    from ctf.models import CTFSpareRange

    status_map = {
        ResourceStatus.READY.value: SpareRangeStatus.READY.value,
        ResourceStatus.FAILED.value: SpareRangeStatus.FAILED.value,
        ResourceStatus.DESTROYED.value: SpareRangeStatus.FAILED.value,
    }
    mapped_status = status_map.get(new_status)
    if mapped_status is None:
        return

    spares = CTFSpareRange.objects.filter(
        range_instance_id=range_instance_id,
        consumed_by__isnull=True,
    )

    updated = 0
    for spare in spares:
        if spare.status != mapped_status:
            spare.status = mapped_status
            terminal = new_status in {ResourceStatus.FAILED.value, ResourceStatus.DESTROYED.value}
            owner = spare.owner_user if terminal else None
            if terminal:
                spare.owner_user = None
                spare.save(update_fields=["status", "owner_user", "updated_at"])
                from ctf.services.range.spares import delete_managed_spare_user

                delete_managed_spare_user(owner)
            else:
                spare.save(update_fields=["status", "updated_at"])
            updated += 1

    if updated:
        logger.info(
            "Synced spare status=%s for %d CTF spare range(s) (range_instance_id=%s, was=%s)",
            mapped_status,
            updated,
            range_instance_id,
            previous_status,
        )
