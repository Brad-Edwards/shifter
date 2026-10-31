"""Field defaults for ``CTFEvent`` that migrations import by path."""

from __future__ import annotations


def default_reminder_hours() -> list[int]:
    """Remind 24 hours and 1 hour before start; an empty list disables reminders (#582)."""
    return [24, 1]
