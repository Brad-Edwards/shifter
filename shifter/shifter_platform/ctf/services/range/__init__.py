"""CTF Range service.

Provides integration with Shifter's range infrastructure for CTF events. This
package facade re-exports the public service surface; implementation is split by
responsibility across private submodules:

- :mod:`ctf.services.range.provision` -- single-participant provisioning + retry
- :mod:`ctf.services.range.batch` -- event-level throttled provisioning
- :mod:`ctf.services.range.tasks` -- spin-up task enqueue + progress projection
- :mod:`ctf.services.range.lifecycle` -- stop/start/restart/destroy + cleanup
- :mod:`ctf.services.range.status` -- range status reads
"""

from __future__ import annotations

from ctf.services.range.batch import provision_event_ranges_throttled
from ctf.services.range.lifecycle import (
    cleanup_event_ranges,
    destroy_participant_range,
    restart_participant_range,
    start_participant_range,
    stop_participant_range,
)
from ctf.services.range.provision import (
    provision_participant_range,
    provision_participant_range_with_retry,
)
from ctf.services.range.status import get_range_status, update_participant_range_status
from ctf.services.range.tasks import get_provision_progress, request_event_provisioning

__all__ = [
    "cleanup_event_ranges",
    "destroy_participant_range",
    "get_provision_progress",
    "get_range_status",
    "provision_event_ranges_throttled",
    "provision_participant_range",
    "provision_participant_range_with_retry",
    "request_event_provisioning",
    "restart_participant_range",
    "start_participant_range",
    "stop_participant_range",
    "update_participant_range_status",
]
