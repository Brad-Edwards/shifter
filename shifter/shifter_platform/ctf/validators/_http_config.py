"""Closed configuration contract for HTTP flag validators."""

from __future__ import annotations

import re
from typing import Any

from ._ssrf import _safe_parse_url, is_blocked_url

DEFAULT_HTTP_TIMEOUT = 10
MAX_HTTP_TIMEOUT = 30
MAX_HTTP_HEADERS = 16
MAX_HTTP_URL_LENGTH = 2048

_ALLOWED_CONFIG_KEYS = frozenset({"url", "method", "timeout", "headers"})
_ALLOWED_METHODS = frozenset({"GET", "POST"})
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
    }
)


class HTTPValidatorConfigError(ValueError):
    """Raised when HTTP validator configuration violates its closed contract."""


def _normalize_url(value: object, *, check_destination: bool) -> str:
    if not isinstance(value, str) or not value:
        raise HTTPValidatorConfigError("validator_config.url is required")
    if len(value) > MAX_HTTP_URL_LENGTH or "\x00" in value:
        raise HTTPValidatorConfigError("validator_config.url is invalid")
    if not value.startswith("https://"):
        raise HTTPValidatorConfigError("validator_config.url must use HTTPS")

    parsed_tuple = _safe_parse_url(value)
    if parsed_tuple is None:
        raise HTTPValidatorConfigError("validator_config.url is invalid")
    parsed, _hostname, _port = parsed_tuple
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        raise HTTPValidatorConfigError("validator_config.url is invalid")
    if check_destination and is_blocked_url(value):
        raise HTTPValidatorConfigError("validator_config.url must not target private or reserved addresses")
    return value


def _normalize_method(value: object) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_METHODS:
        raise HTTPValidatorConfigError("validator_config.method must be GET or POST")
    return value


def _normalize_timeout(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_HTTP_TIMEOUT:
        raise HTTPValidatorConfigError("validator_config.timeout must be an integer between 1 and 30")
    return value


def _normalize_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HTTPValidatorConfigError("validator_config.headers must be an object")
    if len(value) > MAX_HTTP_HEADERS:
        raise HTTPValidatorConfigError("validator_config.headers has too many entries")

    normalized: dict[str, str] = {}
    seen_names: set[str] = set()
    for name, header_value in value.items():
        if not isinstance(name, str) or len(name) > 100 or not _HEADER_NAME_RE.fullmatch(name):
            raise HTTPValidatorConfigError("validator_config contains an invalid header name")
        lowered_name = name.lower()
        if lowered_name in _RESERVED_HEADERS:
            raise HTTPValidatorConfigError("validator_config contains a reserved header name")
        if lowered_name in seen_names:
            raise HTTPValidatorConfigError("validator_config contains duplicate header names")
        if (
            not isinstance(header_value, str)
            or len(header_value) > 2048
            or any(char in header_value for char in ("\x00", "\r", "\n"))
        ):
            raise HTTPValidatorConfigError("validator_config contains an invalid header value")
        seen_names.add(lowered_name)
        normalized[name] = header_value
    return normalized


def normalize_http_validator_config(
    value: object,
    *,
    check_destination: bool = True,
) -> dict[str, Any]:
    """Validate and return the canonical HTTP validator configuration.

    Runtime callers disable the edit-time destination check because their
    pinned resolver is the sole DNS decision and independently rejects every
    unsafe address. Write callers keep the default and reject an unsafe
    destination before persistence.
    """
    if not isinstance(value, dict):
        raise HTTPValidatorConfigError("validator_config is required for HTTP flags")
    unknown_keys = set(value) - _ALLOWED_CONFIG_KEYS
    if unknown_keys:
        raise HTTPValidatorConfigError("validator_config contains unknown fields")

    return {
        "url": _normalize_url(value.get("url"), check_destination=check_destination),
        "method": _normalize_method(value.get("method", "POST")),
        "timeout": _normalize_timeout(value.get("timeout", DEFAULT_HTTP_TIMEOUT)),
        "headers": _normalize_headers(value.get("headers", {})),
    }
