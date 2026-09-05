"""Deployment-owned settings for scoped CTF communications (ADR-051, #2048).

Typed, bounded, server-owned operational policy: how long communication content
and per-recipient state are retained, and which external hosts a safe-content
link may point at. Both fail loudly at startup on a malformed value rather than
silently weakening retention or content policy. Neither ever carries a campaign,
recipient, body, or secret.
"""

from __future__ import annotations

import os
import re

from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "CTF_COMMUNICATION_ALLOWED_LINK_HOSTS",
    "CTF_COMMUNICATION_RETENTION_DAYS",
]

_DEFAULT_RETENTION_DAYS = 90
_MIN_RETENTION_DAYS = 1
_MAX_RETENTION_DAYS = 365

# A bare, normalized hostname: labels of letters/digits/hyphens joined by dots.
# Deliberately excludes schemes, paths, ports, credentials, and whitespace so a
# link-host allowlist entry cannot smuggle a scheme or path into the policy.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def _parse_retention_days(raw: str) -> int:
    """Parse and bound the retention window in days (fail-loud)."""
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise ImproperlyConfigured("SHIFTER_CTF_COMMUNICATION_RETENTION_DAYS must be an integer") from exc
    if not _MIN_RETENTION_DAYS <= value <= _MAX_RETENTION_DAYS:
        raise ImproperlyConfigured(
            f"SHIFTER_CTF_COMMUNICATION_RETENTION_DAYS must be between {_MIN_RETENTION_DAYS} and {_MAX_RETENTION_DAYS}"
        )
    return value


def _parse_allowed_link_hosts(raw: str) -> frozenset[str]:
    """Parse a comma-separated, normalized host allowlist (fail-loud).

    An empty value means no external link hosts are allowed (relative links only).
    """
    hosts: set[str] = set()
    for entry in (raw or "").split(","):
        normalized = entry.strip().lower()
        if not normalized:
            continue
        if not _HOSTNAME_RE.match(normalized):
            raise ImproperlyConfigured(
                f"SHIFTER_CTF_COMMUNICATION_ALLOWED_LINK_HOSTS contains an invalid host: {entry.strip()!r}"
            )
        hosts.add(normalized)
    return frozenset(hosts)


CTF_COMMUNICATION_RETENTION_DAYS = _parse_retention_days(
    os.environ.get("SHIFTER_CTF_COMMUNICATION_RETENTION_DAYS", str(_DEFAULT_RETENTION_DAYS))
)
CTF_COMMUNICATION_ALLOWED_LINK_HOSTS = _parse_allowed_link_hosts(
    os.environ.get("SHIFTER_CTF_COMMUNICATION_ALLOWED_LINK_HOSTS", "")
)
