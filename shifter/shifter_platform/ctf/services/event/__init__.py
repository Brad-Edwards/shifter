"""CTF event service, grouped by lifecycle boundary (#683).

Split from the single ``ctf/services/event.py`` module. The public import
surface is unchanged: every entry point is re-exported here, and late,
call-time imports (the scheduler handlers) resolve through this package,
which is also the patch point tests already target for those flows.
"""

from ctf.services.event.crud import (
    create_event,
    delete_event,
    event_pk_if_exists,
    get_event,
    get_event_stats,
    get_organizer_events,
    list_events_for_organizer,
    update_event,
)
from ctf.services.event.lifecycle import (
    activate_event,
    archive_event,
    cancel_event,
    complete_event,
    end_event,
    force_delete_event,
    open_registration,
    pause_event,
    resume_event,
    schedule_event,
    start_event,
)
from ctf.services.event.scheduling import (
    _cancel_event_tasks,
    _reschedule_event_tasks,
    _schedule_event_tasks,
)

__all__ = [
    "_cancel_event_tasks",
    "_reschedule_event_tasks",
    "_schedule_event_tasks",
    "activate_event",
    "archive_event",
    "cancel_event",
    "complete_event",
    "create_event",
    "delete_event",
    "end_event",
    "event_pk_if_exists",
    "force_delete_event",
    "get_event",
    "get_event_stats",
    "get_organizer_events",
    "list_events_for_organizer",
    "open_registration",
    "pause_event",
    "resume_event",
    "schedule_event",
    "start_event",
    "update_event",
]
