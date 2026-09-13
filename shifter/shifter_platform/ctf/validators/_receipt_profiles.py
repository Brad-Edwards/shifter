"""Deployment-owned registry for authenticated receipt verifier profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from shared.receipt_validation import ReceiptKeyMode

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PROFILES: dict[str, ReceiptVerifierProfile] = {}


class ReceiptProfileError(ValueError):
    """A verifier profile is malformed, unsafe, or conflicts with the registry."""


@dataclass(frozen=True, slots=True)
class ReceiptVerifierProfile:
    """Closed deployment profile for one authenticated verifier endpoint."""

    profile_id: str
    deployment_id: str
    provider_contract: str
    endpoint_url: str
    audience: str
    service_auth_secret_ref: str
    issuer_id: str
    allowed_key_modes: tuple[ReceiptKeyMode, ...]
    allowed_algorithms: tuple[str, ...]
    permitted_objectives: tuple[str, ...]
    total_timeout_seconds: int = 10
    max_addresses: int = 2
    max_request_bytes: int = 8192
    max_response_bytes: int = 4096
    max_receipt_ttl_seconds: int = 900

    def __post_init__(self) -> None:
        for name in ("profile_id", "deployment_id", "provider_contract", "issuer_id"):
            _require_identifier(name, getattr(self, name))
        _require_endpoint(self.endpoint_url)
        _require_audience(self.audience)
        _require_secret_ref(self.service_auth_secret_ref)
        if (
            not isinstance(self.allowed_key_modes, tuple)
            or not self.allowed_key_modes
            or len(set(self.allowed_key_modes)) != len(self.allowed_key_modes)
            or any(not isinstance(mode, ReceiptKeyMode) for mode in self.allowed_key_modes)
        ):
            raise ReceiptProfileError("allowed_key_modes must be a non-empty tuple of unique key modes")
        if (
            not isinstance(self.allowed_algorithms, tuple)
            or not self.allowed_algorithms
            or len(self.allowed_algorithms) > 16
            or len(set(self.allowed_algorithms)) != len(self.allowed_algorithms)
        ):
            raise ReceiptProfileError("allowed_algorithms must contain 1-16 unique identifiers")
        for algorithm in self.allowed_algorithms:
            _require_identifier("algorithm", algorithm)
        if (
            not isinstance(self.permitted_objectives, tuple)
            or not self.permitted_objectives
            or len(self.permitted_objectives) > 64
            or len(set(self.permitted_objectives)) != len(self.permitted_objectives)
        ):
            raise ReceiptProfileError("permitted_objectives must contain 1-64 unique identifiers")
        for objective in self.permitted_objectives:
            _require_identifier("objective", objective)
        _require_bound("total_timeout_seconds", self.total_timeout_seconds, 1, 30)
        _require_bound("max_addresses", self.max_addresses, 1, 8)
        _require_bound("max_request_bytes", self.max_request_bytes, 1024, 16384)
        _require_bound("max_response_bytes", self.max_response_bytes, 256, 16384)
        _require_bound("max_receipt_ttl_seconds", self.max_receipt_ttl_seconds, 1, 3600)


def register_receipt_profile(profile: ReceiptVerifierProfile) -> None:
    """Register a trusted deployment profile, allowing only exact idempotent replay."""
    if not isinstance(profile, ReceiptVerifierProfile):
        raise ReceiptProfileError("profile must be a ReceiptVerifierProfile")
    existing = _PROFILES.get(profile.profile_id)
    if existing is not None and existing != profile:
        raise ReceiptProfileError(f"receipt profile {profile.profile_id!r} is already registered differently")
    _PROFILES[profile.profile_id] = profile


def get_receipt_profile(profile_id: str) -> ReceiptVerifierProfile | None:
    """Return an exact registered profile without fallback or inference."""
    return _PROFILES.get(profile_id)


def _require_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ReceiptProfileError(f"{name} is not a canonical identifier")


def _require_endpoint(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ReceiptProfileError("endpoint_url is invalid")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ReceiptProfileError("endpoint_url is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ReceiptProfileError("endpoint_url must be an HTTPS endpoint without credentials, query, or fragment")


def _require_audience(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ReceiptProfileError("audience is invalid")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ReceiptProfileError("audience must be an HTTPS service identity")


def _require_secret_ref(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ReceiptProfileError("service_auth_secret_ref must be a bounded provider reference")


def _require_bound(name: str, value: object, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ReceiptProfileError(f"{name} must be between {minimum} and {maximum}")
