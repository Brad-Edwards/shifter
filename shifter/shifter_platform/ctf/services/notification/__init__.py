"""CTF notification service, grouped by audience (#683).

Split from the single ``ctf/services/notification.py`` module. The public
import surface is unchanged: every entry point (and the shared email
helpers used by participant account flows) is re-exported here. The
scheduler and participant flows import these names late, at call time, so
this package boundary is also the patch point tests already target.
"""

from ctf.services.notification._email import _build_ctf_login_url, _render_email, _send_email
from ctf.services.notification.delivery import (
    cancel_scheduled_notification,
    deliver_scheduled_notification,
    notify_participant_provision_failure,
    schedule_notification,
    send_announcement,
    send_cleanup_warning,
    send_credentials,
    send_event_results,
    send_invitations,
    send_range_ready,
    send_reminder,
)
from ctf.services.notification.organizer import (
    EVENT_NOT_FOUND_LOG,
    NO_ORGANIZER_EMAIL_LOG,
    notify_organizer_event_end,
    notify_organizer_event_start,
    notify_organizer_provision_failure,
)
from ctf.services.notification.realtime import publish_event_notification

__all__ = [
    "EVENT_NOT_FOUND_LOG",
    "NO_ORGANIZER_EMAIL_LOG",
    "_build_ctf_login_url",
    "_render_email",
    "_send_email",
    "cancel_scheduled_notification",
    "deliver_scheduled_notification",
    "notify_organizer_event_end",
    "notify_organizer_event_start",
    "notify_organizer_provision_failure",
    "notify_participant_provision_failure",
    "publish_event_notification",
    "schedule_notification",
    "send_announcement",
    "send_cleanup_warning",
    "send_credentials",
    "send_event_results",
    "send_invitations",
    "send_range_ready",
    "send_reminder",
]
