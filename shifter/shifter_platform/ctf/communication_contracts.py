"""Closed, versioned contracts for scoped CTF communications (ADR-051, #2048).

Pure, dependency-light validators for the communication domain: the safe
rich-content profile ``ctf-communication-markdown/v1`` plus the closed audience,
trigger, and channel shapes. This module imports no Django models, so both the
domain models' ``clean()`` and the communication services can validate against
one source of truth without a circular import.

Everything here is fail-closed: unknown keys, unknown discriminators, oversized
input, control characters, raw HTML, executable URL schemes, and disallowed link
hosts are *rejected*, never stripped and accepted. It mirrors the exact-key /
closed-vocabulary / byte-bound / digest pattern used by
``shared.operation_envelope`` and ``ctf.content_bundle`` rather than reusing
their unrelated schemas.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from ctf.enums import AcknowledgementPolicy, AudienceKind, CommunicationChannel, TriggerKind
from ctf.exceptions import CTFCommunicationError

CONTENT_PROFILE_V1 = "ctf-communication-markdown/v1"

MAX_SUBJECT_CODEPOINTS = 200
MAX_BODY_BYTES = 65_536
MAX_RENDERED_BYTES = 131_072
MAX_LINK_CHARS = 2_048

_DIGEST_PREFIX = "sha256:"

# Tag-like sequence: ``<`` immediately followed by a letter, ``/``, ``!`` or ``?``.
# Detecting and REJECTING raw HTML (rather than stripping it) is the v1 policy;
# ``<`` used as a literal ("a < b") is unaffected because a space or digit follows.
_HTML_TAG_RE = re.compile(r"<\s*[/!?a-zA-Z]")
# Any control character except tab/newline/carriage-return.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Executable / non-navigational URL schemes rejected anywhere in the body.
_DANGEROUS_SCHEMES = ("javascript:", "data:", "vbscript:", "file:", "blob:")
# Markdown inline link/image: [text](url) and ![alt](url).
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)(?:\s+[\"'(][^)]*)?\s*\)")
# Markdown reference-style link/image definition: [label]: url
_MARKDOWN_REF_DEF_RE = re.compile(r"(?m)^[ \t]{0,3}\[[^\]]+\]:[ \t]*(\S+)")
# Bare scheme-prefixed URL anywhere (GFM autolinks these when rendered).
_BARE_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>()\[\]\"'`\\]+")


def _reject(message: str, *, code: str = "CTF_COMMUNICATION_CONTENT_INVALID") -> CTFCommunicationError:
    """Build a domain error with a stable, body-free code."""
    return CTFCommunicationError(message, code=code)


def canonical_digest(payload: dict) -> str:
    """Return a deterministic, key-order-independent content digest."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Safe rich-content profile
# ---------------------------------------------------------------------------


def _validate_subject(subject: object) -> str:
    if not isinstance(subject, str):
        raise _reject("subject must be a string")
    trimmed = subject.strip()
    if not trimmed:
        raise _reject("subject is required")
    if _CONTROL_RE.search(trimmed):
        raise _reject("subject contains control characters")
    if len(trimmed) > MAX_SUBJECT_CODEPOINTS:
        raise _reject(f"subject exceeds {MAX_SUBJECT_CODEPOINTS} characters")
    return trimmed


def _validate_absolute_url(url: str, allowed_link_hosts: frozenset[str]) -> None:
    """Validate an absolute destination: https, no credentials, allowlisted host."""
    if len(url) > MAX_LINK_CHARS:
        raise _reject("link exceeds the maximum length")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise _reject("external links must use https")
    if parts.username or parts.password:
        raise _reject("links must not embed credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise _reject("link has no host")
    if host == "localhost":
        raise _reject("link host is not allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _reject("link host must not be an IP literal")
    if host not in {h.lower() for h in allowed_link_hosts}:
        raise _reject("link host is not in the allowlist")


def _validate_link_target(target: str, allowed_link_hosts: frozenset[str]) -> None:
    """Validate one Markdown link/image destination against the safe profile.

    Only a fragment (``#...``) or an unambiguous same-origin path (a single
    leading ``/`` with no host) is allowed without a host check. A network-path
    ``//host`` reference (which a browser resolves as an external origin) and any
    backslash trick are rejected; anything else must be an allowlisted absolute
    ``https`` URL.
    """
    if len(target) > MAX_LINK_CHARS:
        raise _reject("link exceeds the maximum length")
    if "\\" in target:
        raise _reject("links must not contain backslashes")
    lowered = target.lower()
    for scheme in _DANGEROUS_SCHEMES:
        if lowered.startswith(scheme):
            raise _reject("link uses a disallowed URL scheme")
    if target.startswith("#"):
        return
    if target.startswith("//"):
        raise _reject("protocol-relative links are not allowed")
    if target.startswith("/"):
        return
    _validate_absolute_url(target, allowed_link_hosts)


def _validate_body(body: object, allowed_link_hosts: frozenset[str]) -> str:
    if not isinstance(body, str):
        raise _reject("body must be a string")
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES:
        raise _reject(f"body exceeds {MAX_BODY_BYTES} bytes")
    if _HTML_TAG_RE.search(body):
        raise _reject("raw HTML is not allowed")
    lowered = body.lower()
    for scheme in _DANGEROUS_SCHEMES:
        if scheme in lowered:
            raise _reject("body references a disallowed URL scheme")
    # Inline links/images and reference-style definitions: validate the target.
    for match in _MARKDOWN_LINK_RE.finditer(body):
        _validate_link_target(match.group(1), allowed_link_hosts)
    for match in _MARKDOWN_REF_DEF_RE.finditer(body):
        _validate_link_target(match.group(1), allowed_link_hosts)
    # Bare scheme-prefixed URLs (autolinked at render): every host must be allowed.
    for match in _BARE_URL_RE.finditer(body):
        _validate_absolute_url(match.group(0), allowed_link_hosts)
    return body


def validate_message_content(content: object, *, allowed_link_hosts: frozenset[str]) -> dict:
    """Validate one message revision's content against ``ctf-communication-markdown/v1``.

    Returns a normalized ``{subject, body, profile, digest}`` mapping. The digest
    is stable across equal content so a retry or duplicate revision is detectable
    and an immutable revision can be fenced by identity.
    """
    if not isinstance(content, dict):
        raise _reject("content must be an object")
    unexpected = sorted(set(content) - {"subject", "body"})
    if unexpected:
        raise _reject(f"content has unexpected field(s): {', '.join(unexpected)}")
    subject = _validate_subject(content.get("subject"))
    body = _validate_body(content.get("body"), allowed_link_hosts)
    normalized = {"subject": subject, "body": body, "profile": CONTENT_PROFILE_V1}
    return {**normalized, "digest": canonical_digest(normalized)}


# ---------------------------------------------------------------------------
# Closed audience selector
# ---------------------------------------------------------------------------


def _require_uuid_list(spec: dict, key: str, *, minimum: int, exact: int | None = None) -> list[str]:
    raw = spec.get(key)
    if not isinstance(raw, list) or not raw:
        raise _reject(f"audience {key} must be a non-empty list")
    normalized: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise _reject(f"audience {key} must contain UUID strings")
        try:
            normalized.append(str(UUID(value)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise _reject(f"audience {key} must contain valid UUIDs") from exc
    if exact is not None and len(normalized) != exact:
        raise _reject(f"audience {key} must contain exactly {exact} id(s)")
    if len(normalized) < minimum:
        raise _reject(f"audience {key} must contain at least {minimum} id(s)")
    return normalized


_AUDIENCE_KEYS_BY_KIND: dict[str, str] = {
    AudienceKind.PARTICIPANT.value: "participant_ids",
    AudienceKind.PARTICIPANT_SET.value: "participant_ids",
    AudienceKind.TEAM.value: "team_ids",
    AudienceKind.EVENT.value: "event_ids",
    AudienceKind.MULTI_EVENT.value: "event_ids",
}


def validate_audience_spec(spec: object) -> dict:
    """Validate a closed audience selector; return the normalized mapping.

    Stores public CTF UUIDs only. Email addresses, arbitrary user ids, ORM
    predicates, and free-form filter JSON are rejected by the exact-key rule.
    """
    if not isinstance(spec, dict):
        raise _reject("audience must be an object")
    kind = spec.get("kind")
    if kind not in _AUDIENCE_KEYS_BY_KIND:
        raise _reject("audience kind is not supported")
    id_key = _AUDIENCE_KEYS_BY_KIND[kind]
    unexpected = sorted(set(spec) - {"kind", id_key})
    if unexpected:
        raise _reject(f"audience has unexpected field(s): {', '.join(unexpected)}")
    if kind == AudienceKind.PARTICIPANT.value or kind == AudienceKind.EVENT.value:
        ids = _require_uuid_list(spec, id_key, minimum=1, exact=1)
    elif kind == AudienceKind.MULTI_EVENT.value:
        ids = _require_uuid_list(spec, id_key, minimum=2)
    else:
        ids = _require_uuid_list(spec, id_key, minimum=1)
    return {"kind": kind, id_key: ids}


# ---------------------------------------------------------------------------
# Closed trigger declaration
# ---------------------------------------------------------------------------


_TRIGGER_KEYS_BY_KIND: dict[str, frozenset[str]] = {
    TriggerKind.MANUAL.value: frozenset(),
    TriggerKind.ABSOLUTE_TIME.value: frozenset({"due_at"}),
    TriggerKind.EVENT_LIFECYCLE.value: frozenset({"event_status"}),
    TriggerKind.RAES_OCCURRENCE.value: frozenset({"declaration_ref", "occurrence_ref"}),
    TriggerKind.RANGE_SIGNAL.value: frozenset({"declaration_ref"}),
}


def validate_trigger_spec(spec: object) -> dict:
    """Validate a closed trigger declaration; return the normalized mapping.

    A trigger is data, never code: no callables, webhooks, or plugin entry points.
    """
    if not isinstance(spec, dict):
        raise _reject("trigger must be an object")
    kind = spec.get("kind")
    if kind not in _TRIGGER_KEYS_BY_KIND:
        raise _reject("trigger kind is not supported")
    required = _TRIGGER_KEYS_BY_KIND[kind]
    allowed = {"kind"} | required
    unexpected = sorted(set(spec) - allowed)
    if unexpected:
        raise _reject(f"trigger has unexpected field(s): {', '.join(unexpected)}")
    normalized = {"kind": kind}
    for key in sorted(required):
        value = spec.get(key)
        if not isinstance(value, str) or not value.strip():
            raise _reject(f"trigger requires a non-empty '{key}'")
        normalized[key] = value.strip()
    return normalized


# ---------------------------------------------------------------------------
# Channels and acknowledgement policy
# ---------------------------------------------------------------------------


def validate_channels(channels: object) -> list[str]:
    """Validate a non-empty, duplicate-free subset of the closed channel set."""
    if not isinstance(channels, list) or not channels:
        raise _reject("at least one channel is required")
    allowed = {c.value for c in CommunicationChannel}
    seen: list[str] = []
    for channel in channels:
        if channel not in allowed:
            raise _reject("unknown channel")
        if channel in seen:
            raise _reject("channels must not repeat")
        seen.append(channel)
    return seen


def validate_acknowledgement_policy(policy: object) -> str:
    """Validate the acknowledgement policy against the closed vocabulary."""
    allowed = {p.value for p in AcknowledgementPolicy}
    if policy not in allowed:
        raise _reject("unknown acknowledgement policy")
    return policy
