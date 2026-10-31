"""Self-service participant profile updates (CTF-610).

The CTF layer keeps no separate profile store: display name, affiliation, and
time zone (#582) live on the event-scoped ``CTFParticipant`` row, and the username belongs to
the platform account (see ``accounts.rename_own_participant_username``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from ctf.exceptions import CTFNotFoundError, CTFValidationError
from ctf.models import CTFParticipant
from shared.time_zones import is_valid_time_zone

if TYPE_CHECKING:
    from uuid import UUID

    from django.contrib.auth.models import AnonymousUser, User

logger = logging.getLogger(__name__)

_NAME_MAX = 100
_AFFILIATION_MAX = 120


def _cleaned_name(name: str) -> str:
    """Return the stripped display name, or refuse an empty or oversized one."""
    cleaned = name.strip()
    if not cleaned:
        raise CTFValidationError("Display name cannot be empty", code="CTF_INVALID_NAME")
    if len(cleaned) > _NAME_MAX:
        raise CTFValidationError("Display name is too long", code="CTF_INVALID_NAME", details={"max_length": _NAME_MAX})
    return cleaned


def _cleaned_affiliation(affiliation: str) -> str:
    """Return the stripped affiliation, or refuse an oversized one."""
    cleaned = affiliation.strip()
    if len(cleaned) > _AFFILIATION_MAX:
        raise CTFValidationError(
            "Affiliation is too long", code="CTF_INVALID_AFFILIATION", details={"max_length": _AFFILIATION_MAX}
        )
    return cleaned


def _cleaned_time_zone(time_zone: str) -> str:
    """Return the stripped time zone, or refuse a name that is not installed (#582)."""
    cleaned = time_zone.strip()
    if cleaned and not is_valid_time_zone(cleaned):
        raise CTFValidationError("Unknown time zone", code="CTF_INVALID_TIMEZONE")
    return cleaned


def update_own_profile(
    participant_id: UUID,
    *,
    actor: User | AnonymousUser,
    name: str | None = None,
    affiliation: str | None = None,
    time_zone: str | None = None,
) -> CTFParticipant:
    """Update the actor's own display name, affiliation, and/or time zone.

    ``None`` leaves a field unchanged; an empty affiliation or time zone clears
    it. A time zone must be an installed IANA name (#582). The
    display name can never be cleared — it identifies the participant on
    every organizer surface.
    """
    with transaction.atomic():
        try:
            participant = CTFParticipant.objects.select_for_update(of=("self",)).get(
                pk=participant_id, deleted_at__isnull=True
            )
        except CTFParticipant.DoesNotExist:
            raise CTFNotFoundError("Participant not found", details={"participant_id": str(participant_id)}) from None
        if participant.user_id != actor.pk:
            raise CTFValidationError("You may only edit your own profile", code="CTF_PERMISSION_DENIED")

        requested = {"name": name, "affiliation": affiliation, "time_zone": time_zone}
        cleaners = {"name": _cleaned_name, "affiliation": _cleaned_affiliation, "time_zone": _cleaned_time_zone}
        changes = {field: cleaners[field](value) for field, value in requested.items() if value is not None}
        for field, value in changes.items():
            setattr(participant, field, value)
        update_fields = list(changes)
        if update_fields:
            participant.save(update_fields=[*update_fields, "updated_at"])
    logger.info("Participant %s updated own profile (%s)", participant_id, ", ".join(update_fields) or "no-op")
    return participant
