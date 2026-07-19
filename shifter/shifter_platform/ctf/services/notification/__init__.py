"""CTF Notification service.

Provides business logic for email notifications. The implementation is
split across private submodules (``_participant``, ``_organizer``,
``_email``) and re-exported here so callers continue to use
``from ctf.services.notification import X`` / ``from ctf.services import
notification``.

The re-exports also rebind names that tests historically patch at
``ctf.services.notification.<name>`` (``CTFEvent``, ``CTFParticipant``,
``CTFNotification``, plus the participant/organizer send functions and the
``_send_email`` / ``_render_email`` email helpers) so existing
``unittest.mock.patch`` targets still work.

PATCH LOCALITY: ``_participant.py`` and ``_organizer.py`` never bind
``CTFEvent`` / ``CTFParticipant`` / ``CTFNotification`` / ``_send_email`` /
``_render_email`` into their own module namespace at import time. Instead
they resolve those names through this package at call time
(``from ctf.services import notification as _n``, then e.g.
``_n.CTFEvent...`` / ``_n._send_email(...)``), so a
``patch("ctf.services.notification.<name>")`` mutates the single attribute
those submodules actually look up when they run.
"""

from __future__ import annotations

# --- Names tests patch via ``patch("ctf.services.notification.X")`` --------
# Rebound here so the patch target resolves at the package level; submodules
# look these up through ``ctf.services.notification`` at call time (see the
# PATCH LOCALITY note above), so patches applied here are honoured for free.
from ctf.models import CTFEvent, CTFNotification, CTFParticipant

from ._cleanup import send_cleanup_warning
from ._email import _build_ctf_login_url, _render_email, _send_email
from ._organizer import (
    notify_organizer_event_end,
    notify_organizer_event_start,
    notify_organizer_provision_failure,
)
from ._participant import (
    schedule_notification,
    send_announcement,
    send_credentials,
    send_invitations,
    send_reminder,
)

__all__ = (
    "CTFEvent",
    "CTFNotification",
    "CTFParticipant",
    "_build_ctf_login_url",
    "_render_email",
    "_send_email",
    "notify_organizer_event_end",
    "notify_organizer_event_start",
    "notify_organizer_provision_failure",
    "schedule_notification",
    "send_announcement",
    "send_cleanup_warning",
    "send_credentials",
    "send_invitations",
    "send_reminder",
)
