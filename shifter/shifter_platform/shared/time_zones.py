"""IANA time-zone names accepted from users (#582).

Only names in the installed IANA database pass. ``zoneinfo.available_timezones()``
scans the time-zone path on every call, so the set is cached for the process.
"""

from __future__ import annotations

from functools import cache
from zoneinfo import available_timezones


@cache
def _names() -> frozenset[str]:
    """Return the installed IANA names, read once for the process."""
    return frozenset(available_timezones())


def is_valid_time_zone(name: str) -> bool:
    """Return whether ``name`` is an installed IANA time-zone name."""
    return name in _names()


@cache
def time_zone_choices() -> tuple[tuple[str, str], ...]:
    """Return sorted ``(name, name)`` choices for every installed time zone."""
    return tuple((name, name) for name in sorted(_names()))
