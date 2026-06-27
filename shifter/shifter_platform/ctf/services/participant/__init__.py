"""CTF Participant service.

Business logic for participant management, split across cohesive submodules
(issue #889, python:S104):

- ``lifecycle``: invite/resend/delete/disqualify and profile helpers;
- ``invite_exchange``: one-time invite token exchange and pending completion;
- ``bulk_import``: CSV parsing and bulk import;
- ``queries``: eligibility predicates and participant lookups.

Public names are re-exported here so ``from ctf.services.participant import X``
keeps working unchanged.
"""

from __future__ import annotations

from .bulk_import import bulk_import_participants
from .invite_exchange import (
    InviteExchangeResult,
    complete_pending_invite,
    exchange_invite_token,
)
from .lifecycle import (
    delete_participant,
    disqualify_participant,
    invite_participant,
    resend_invite,
)
from .queries import (
    eligible_participant_q,
    get_participant,
    get_participant_by_user,
    is_active_participant,
    list_participants_for_event,
)

__all__ = [
    "InviteExchangeResult",
    "bulk_import_participants",
    "complete_pending_invite",
    "delete_participant",
    "disqualify_participant",
    "eligible_participant_q",
    "exchange_invite_token",
    "get_participant",
    "get_participant_by_user",
    "invite_participant",
    "is_active_participant",
    "list_participants_for_event",
    "resend_invite",
]
