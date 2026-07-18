"""Closed contracts for participant-held remote-access credentials.

Only non-secret binding metadata crosses the range-substrate boundary. Profile
content is resolved from the provider secret store and validated in memory at
the Engine access boundary before delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

OPENVPN_CAPABILITY_VERSION = "openvpn-capability-v1"
OPENVPN_BINDING_VERSION = "openvpn-binding-v1"
OPENVPN_PROFILE_VERSION = "openvpn-profile-v1"
OPENVPN_PROFILE_MEDIA_TYPE = "application/x-openvpn-profile"
OPENVPN_PROFILE_MAX_BYTES = 64 * 1024

_BINDING_KEYS = {
    "version",
    "channel",
    "generation",
    "owner_user_id",
    "target_ref",
    "endpoint",
    "port",
    "profile_version",
    "secret_ref",
    "ready",
}
_CAPABILITY_KEYS = {"version", "channel", "target_ref", "teardown_at"}
OPENVPN_CAPABILITY_MAX_WINDOW = timedelta(days=397)
_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    r"(?:\.(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*\Z"
)
_INLINE_BLOCKS = frozenset({"ca", "cert", "key", "tls-crypt"})
_NO_ARGUMENT_DIRECTIVES = frozenset({"client", "nobind", "persist-key", "persist-tun", "auth-nocache"})
_ONE_ARGUMENT_DIRECTIVES = {
    "dev": frozenset({"tun"}),
    "proto": frozenset({"udp", "udp4"}),
    "resolv-retry": frozenset({"infinite"}),
    "remote-cert-tls": frozenset({"server"}),
    "verb": frozenset({"3"}),
    "auth": frozenset({"SHA256"}),
    "cipher": frozenset({"AES-256-GCM"}),
    "tls-version-min": frozenset({"1.2"}),
}


class OpenVpnBindingError(ValueError):
    """A remote-access binding or resolved profile violated its closed shape."""


@dataclass(frozen=True)
class OpenVpnBinding:
    """Validated non-secret OpenVPN access metadata for one range generation."""

    generation: UUID
    owner_user_id: int
    target_ref: UUID
    endpoint: str
    port: int
    secret_ref: str
    ready: bool
    channel: str = "openvpn"
    version: str = OPENVPN_BINDING_VERSION
    profile_version: str = OPENVPN_PROFILE_VERSION


@dataclass(frozen=True)
class OpenVpnCapability:
    """Server-issued authorization to provision one generation-bound VPN edge."""

    target_ref: UUID
    teardown_at: datetime
    channel: str = "openvpn"
    version: str = OPENVPN_CAPABILITY_VERSION

    def as_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation persisted by Engine."""
        return {
            "version": self.version,
            "channel": self.channel,
            "target_ref": str(self.target_ref),
            "teardown_at": self.teardown_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class OpenVpnProfile:
    """In-memory profile returned only after current access checks pass."""

    content: bytes
    generation: UUID
    profile_version: str


def _require_uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise OpenVpnBindingError(f"{field} must be a UUID") from exc


def _require_utc_datetime(value: object, field: str) -> datetime:
    """Parse one canonical timezone-aware RFC 3339 timestamp."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise OpenVpnBindingError(f"{field} must be a UTC RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OpenVpnBindingError(f"{field} must be a UTC RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise OpenVpnBindingError(f"{field} must be a UTC RFC 3339 timestamp")
    return parsed.astimezone(UTC)


def parse_openvpn_capability(value: object) -> OpenVpnCapability:
    """Parse the exact server-issued provisioning authorization shape."""
    if not isinstance(value, dict):
        raise OpenVpnBindingError("capability must be an object")
    keys = set(value)
    if keys != _CAPABILITY_KEYS:
        unknown = sorted(keys - _CAPABILITY_KEYS)
        missing = sorted(_CAPABILITY_KEYS - keys)
        detail = f"unknown fields {unknown}" if unknown else f"missing fields {missing}"
        raise OpenVpnBindingError(f"capability has {detail}")
    if value["version"] != OPENVPN_CAPABILITY_VERSION:
        raise OpenVpnBindingError("unsupported capability version")
    if value["channel"] != "openvpn":
        raise OpenVpnBindingError("capability channel must be openvpn")
    return OpenVpnCapability(
        target_ref=_require_uuid(value["target_ref"], "target_ref"),
        teardown_at=_require_utc_datetime(value["teardown_at"], "teardown_at"),
    )


def build_openvpn_capability(target_ref: object, teardown_at: datetime) -> dict[str, object]:
    """Build a canonical capability from trusted server-owned lifecycle facts."""
    if not isinstance(teardown_at, datetime):
        raise OpenVpnBindingError("teardown_at must be a timezone-aware datetime")
    normalized_teardown = _require_utc_datetime(teardown_at.isoformat(), "teardown_at")
    if normalized_teardown.microsecond:
        normalized_teardown = normalized_teardown.replace(microsecond=0) + timedelta(seconds=1)
    capability = OpenVpnCapability(
        target_ref=_require_uuid(target_ref, "target_ref"),
        teardown_at=normalized_teardown,
    )
    validate_openvpn_capability_window(capability)
    return capability.as_dict()


def validate_openvpn_capability_window(
    capability: OpenVpnCapability,
    *,
    now: datetime | None = None,
) -> None:
    """Reject stale or unbounded credential windows before provider mutation."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if capability.teardown_at <= current:
        raise OpenVpnBindingError("OpenVPN teardown deadline must be in the future")
    if capability.teardown_at - current > OPENVPN_CAPABILITY_MAX_WINDOW:
        raise OpenVpnBindingError(
            f"OpenVPN teardown deadline exceeds the {OPENVPN_CAPABILITY_MAX_WINDOW.days}-day maximum"
        )


def parse_openvpn_binding(value: object) -> OpenVpnBinding:
    """Parse the exact non-secret OpenVPN binding shape; reject extensions."""
    if not isinstance(value, dict):
        raise OpenVpnBindingError("binding must be an object")
    keys = set(value)
    if keys != _BINDING_KEYS:
        unknown = sorted(keys - _BINDING_KEYS)
        missing = sorted(_BINDING_KEYS - keys)
        detail = f"unknown fields {unknown}" if unknown else f"missing fields {missing}"
        raise OpenVpnBindingError(f"binding has {detail}")
    if value["version"] != OPENVPN_BINDING_VERSION:
        raise OpenVpnBindingError("unsupported binding version")
    if value["channel"] != "openvpn":
        raise OpenVpnBindingError("channel must be openvpn")
    if value["profile_version"] != OPENVPN_PROFILE_VERSION:
        raise OpenVpnBindingError("unsupported profile_version")
    if isinstance(value["owner_user_id"], bool) or not isinstance(value["owner_user_id"], int):
        raise OpenVpnBindingError("owner_user_id must be a positive integer")
    if value["owner_user_id"] <= 0:
        raise OpenVpnBindingError("owner_user_id must be a positive integer")
    if isinstance(value["port"], bool) or not isinstance(value["port"], int) or not 1 <= value["port"] <= 65535:
        raise OpenVpnBindingError("port must be between 1 and 65535")
    if not isinstance(value["ready"], bool):
        raise OpenVpnBindingError("ready must be a boolean")
    endpoint = value["endpoint"]
    if not isinstance(endpoint, str) or not _HOST_RE.fullmatch(endpoint):
        raise OpenVpnBindingError("endpoint must be a bounded hostname or address")
    secret_ref = value["secret_ref"]
    if (
        not isinstance(secret_ref, str)
        or not secret_ref
        or len(secret_ref) > 500
        or secret_ref != secret_ref.strip()
        or "\n" in secret_ref
        or "\r" in secret_ref
    ):
        raise OpenVpnBindingError("secret_ref must be a bounded single-line provider reference")
    return OpenVpnBinding(
        generation=_require_uuid(value["generation"], "generation"),
        owner_user_id=value["owner_user_id"],
        target_ref=_require_uuid(value["target_ref"], "target_ref"),
        endpoint=endpoint,
        port=value["port"],
        secret_ref=secret_ref,
        ready=value["ready"],
    )


def _validate_directive(parts: list[str], binding: OpenVpnBinding) -> None:
    name = parts[0]
    if name in _NO_ARGUMENT_DIRECTIVES and len(parts) == 1:
        return
    allowed = _ONE_ARGUMENT_DIRECTIVES.get(name)
    if allowed is not None and len(parts) == 2 and parts[1] in allowed:
        return
    if name == "remote" and parts == ["remote", binding.endpoint, str(binding.port)]:
        return
    if name == "data-ciphers" and parts == ["data-ciphers", "AES-256-GCM:AES-128-GCM"]:
        return
    raise OpenVpnBindingError(f"profile contains forbidden or malformed directive {name!r}")


def validate_openvpn_profile(profile: str, binding: OpenVpnBinding) -> bytes:
    """Return validated UTF-8 profile bytes for the exact current binding."""
    if not isinstance(profile, str) or not profile or "\x00" in profile or "\r" in profile:
        raise OpenVpnBindingError("profile must be non-empty normalized UTF-8 text")
    encoded = profile.encode("utf-8")
    if len(encoded) > OPENVPN_PROFILE_MAX_BYTES:
        raise OpenVpnBindingError("profile exceeds the maximum size")

    open_block: str | None = None
    seen_blocks: set[str] = set()
    seen_directives: set[str] = set()
    for raw_line in profile.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if open_block is not None:
            if line == f"</{open_block}>":
                seen_blocks.add(open_block)
                open_block = None
            elif line.startswith("<"):
                raise OpenVpnBindingError("profile contains a malformed inline credential block")
            continue
        if line.startswith("<") and line.endswith(">") and not line.startswith("</"):
            block = line[1:-1]
            if block not in _INLINE_BLOCKS or block in seen_blocks:
                raise OpenVpnBindingError("profile contains an unsupported inline credential block")
            open_block = block
            continue
        parts = line.split()
        _validate_directive(parts, binding)
        seen_directives.add(parts[0])
    if open_block is not None:
        raise OpenVpnBindingError("profile has an unterminated inline credential block")
    required_directives = {"client", "dev", "proto", "remote", "nobind", "remote-cert-tls", "auth-nocache"}
    if not required_directives.issubset(seen_directives) or seen_blocks != _INLINE_BLOCKS:
        raise OpenVpnBindingError("profile is missing required directives or inline credentials")
    return encoded


def openvpn_binding_available(value: object, owner_user_id: int) -> bool:
    """Return whether a binding is ready and belongs to the current range owner."""
    try:
        binding = parse_openvpn_binding(value)
    except OpenVpnBindingError:
        return False
    return binding.ready and binding.owner_user_id == owner_user_id
