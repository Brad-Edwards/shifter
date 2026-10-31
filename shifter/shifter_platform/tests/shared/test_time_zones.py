"""IANA time-zone allowlist (#582)."""

from __future__ import annotations

import pytest

from shared.time_zones import is_valid_time_zone, time_zone_choices


@pytest.mark.parametrize("name", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_installed_names_are_valid(name):
    assert is_valid_time_zone(name)


@pytest.mark.parametrize("name", ["", "Mars/Olympus", "../etc/passwd", "/etc/localtime", "america/new_york", "UTC "])
def test_other_names_are_not_valid(name):
    assert not is_valid_time_zone(name)


def test_choices_are_sorted_installed_names():
    names = [value for value, _label in time_zone_choices()]
    assert names == sorted(names)
    assert "Europe/Paris" in names
    assert all(is_valid_time_zone(name) for name in names)
