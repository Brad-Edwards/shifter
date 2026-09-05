"""Bounded, fail-loud communication settings parsers (ADR-051, #2048).

The retention window and link-host allowlist are typed, bounded, server-owned
settings. Malformed values must fail loudly at startup rather than silently
weaken content policy or retention. These test the pure parsers directly.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config._ctf_communication_settings import _parse_allowed_link_hosts, _parse_retention_days


def test_retention_days_defaults_and_bounds():
    assert _parse_retention_days("90") == 90
    assert _parse_retention_days("1") == 1
    assert _parse_retention_days("365") == 365


@pytest.mark.parametrize("raw", ["0", "366", "-5", "abc", ""])
def test_retention_days_rejects_out_of_range_or_nonnumeric(raw):
    with pytest.raises(ImproperlyConfigured):
        _parse_retention_days(raw)


def test_allowed_link_hosts_parses_and_normalizes():
    assert _parse_allowed_link_hosts("Docs.Example.com, cdn.example.com") == frozenset(
        {"docs.example.com", "cdn.example.com"}
    )


def test_allowed_link_hosts_empty_is_no_external_hosts():
    assert _parse_allowed_link_hosts("") == frozenset()


@pytest.mark.parametrize(
    "raw",
    [
        "https://docs.example.com",  # scheme
        "docs.example.com/path",  # path
        "bad host.com",  # space
        "user@docs.example.com",  # credentials
    ],
)
def test_allowed_link_hosts_rejects_malformed_entries(raw):
    with pytest.raises(ImproperlyConfigured):
        _parse_allowed_link_hosts(raw)
