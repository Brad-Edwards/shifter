"""Flag hashing, verification, validation, and flag CRUD."""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from typing import Any
from uuid import UUID

from ctf.exceptions import CTFValidationError
from ctf.models import (
    CTFChallenge,
    CTFFlag,
)

logger = logging.getLogger(__name__)

# Use bcrypt for flag hashing (secure and includes salt)
try:
    import bcrypt

    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    logger.warning("bcrypt not available, using SHA256 for flag hashing (less secure)")


def hash_flag(flag: str, case_sensitive: bool = True) -> str:
    """Hash a flag for secure storage.

    Uses bcrypt if available, falls back to PBKDF2-SHA256.

    Args:
        flag: The plaintext flag value.
        case_sensitive: If False, normalize to lowercase before hashing.

    Returns:
        Hashed flag string for storage.
    """
    value = flag if case_sensitive else flag.lower()
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    else:
        salt = secrets.token_hex(16)
        hash_value = hashlib.pbkdf2_hmac(
            "sha256", value.encode("utf-8"), salt.encode("utf-8"), iterations=600_000
        ).hex()
        return f"pbkdf2:{salt}:{hash_value}"


def _verify_bcrypt_hash(submitted_flag: str, stored_hash: str, context_id: UUID) -> bool:
    """Verify a submitted flag against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            submitted_flag.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except Exception as e:
        logger.exception("Flag verification error for %s: %s", context_id, e)
        return False


def _verify_pbkdf2_hash(submitted_flag: str, stored_hash: str, context_id: UUID) -> bool:
    """Verify a submitted flag against a ``pbkdf2:salt:hash`` value."""
    parts = stored_hash.split(":", 2)
    if len(parts) != 3:
        logger.error("Invalid hash format for %s", context_id)
        return False
    _, salt, expected_hash = parts
    actual_hash = hashlib.pbkdf2_hmac(
        "sha256", submitted_flag.encode("utf-8"), salt.encode("utf-8"), iterations=600_000
    ).hex()
    return secrets.compare_digest(actual_hash, expected_hash)


def _verify_legacy_sha256_hash(submitted_flag: str, stored_hash: str, context_id: UUID) -> bool:
    """Verify a submitted flag against the legacy ``sha256:salt:hash`` value.

    Single-round salted SHA-256, kept only for hashes created before the
    PBKDF2 migration. New flags always use bcrypt or PBKDF2 (see hash_flag()).
    """
    parts = stored_hash.split(":", 2)
    if len(parts) != 3:
        logger.error("Invalid hash format for %s", context_id)
        return False
    _, salt, expected_hash = parts
    actual_hash = hashlib.sha256(  # NOSONAR — legacy compat, not for new hashes
        f"{salt}:{submitted_flag}".encode()
    ).hexdigest()
    return secrets.compare_digest(actual_hash, expected_hash)


def _hash_verifier(stored_hash: str) -> Callable[[str, str, UUID], bool] | None:
    """Resolve the verifier matching a stored hash's scheme, or None."""
    if BCRYPT_AVAILABLE and stored_hash.startswith("$2"):
        return _verify_bcrypt_hash
    for prefix, verifier in (("pbkdf2:", _verify_pbkdf2_hash), ("sha256:", _verify_legacy_sha256_hash)):
        if stored_hash.startswith(prefix):
            return verifier
    return None


def _verify_hash(submitted_flag: str, stored_hash: str, context_id: UUID) -> bool:
    """Verify a submitted flag against a stored hash.

    Args:
        submitted_flag: The flag value to check (already case-normalized if needed).
        stored_hash: The stored hash to compare against.
        context_id: ID for logging (challenge or flag ID).

    Returns:
        True if the flag matches the hash.
    """
    verifier = _hash_verifier(stored_hash)
    if verifier is None:
        logger.error("Unknown hash format for %s", context_id)
        return False
    return verifier(submitted_flag, stored_hash, context_id)


def _verify_regex_flag(flag_obj: CTFFlag, submitted_flag: str) -> bool:
    """Verify a submitted flag against a regex CTFFlag.

    The organizer-authored pattern (stored plaintext in ``flag_hash``) runs on a
    request worker against participant input, so evaluation is bounded and fails
    closed via ``ctf.services.regex_policy`` (issue #1183, ReDoS / CWE-1333).
    """
    from ctf.services.regex_policy import safe_fullmatch

    return safe_fullmatch(flag_obj.flag_hash, submitted_flag, case_sensitive=flag_obj.case_sensitive)


def _verify_programmable_flag(flag_obj: CTFFlag, submitted_flag: str, config: dict[str, Any]) -> bool:
    """Verify a submitted flag against a programmable validator CTFFlag."""
    from ctf.validators import get_validator

    validator_name = config.get("validator_name", "")
    validator_func = get_validator(validator_name)
    if validator_func is None:
        logger.error("Unknown validator %r for flag %s", validator_name, flag_obj.id)
        return False
    try:
        return validator_func(submitted_flag, config.get("params", {}))
    except Exception as e:
        logger.exception("Validator %r error for flag %s: %s", validator_name, flag_obj.id, e)
        return False


def _verify_http_flag(flag_obj: CTFFlag, submitted_flag: str, config: dict[str, Any]) -> bool:
    """Verify a submitted flag against an HTTP validator CTFFlag."""
    from ctf.validators import validate_http

    try:
        return validate_http(submitted_flag, config, flag_obj.challenge_id)
    except Exception as e:
        logger.exception("HTTP validator error for flag %s: %s", flag_obj.id, e)
        return False


def _verify_static_flag(flag_obj: CTFFlag, submitted_flag: str) -> bool:
    """Verify a submitted flag against a static (hashed) CTFFlag."""
    # Static flags: hashed comparison
    value = submitted_flag if flag_obj.case_sensitive else submitted_flag.lower()
    return _verify_hash(value, flag_obj.flag_hash, flag_obj.id)


def verify_single_flag(flag_obj: CTFFlag, submitted_flag: str) -> bool:
    """Verify a submitted flag against a single CTFFlag record.

    Args:
        flag_obj: The CTFFlag instance to verify against.
        submitted_flag: The flag submitted by the participant.

    Returns:
        True if the flag matches.
    """
    # CTF-1401: extension-registered validators win over built-in dispatch.
    from ctf.extensions import get_flag_validator

    custom = get_flag_validator(flag_obj.flag_type)
    if custom is not None:
        result = bool(custom(flag_obj, submitted_flag))
    elif flag_obj.flag_type == "regex":
        result = _verify_regex_flag(flag_obj, submitted_flag)
    elif flag_obj.flag_type in ("programmable", "http"):
        config = flag_obj.validator_config or {}
        verifier = _verify_programmable_flag if flag_obj.flag_type == "programmable" else _verify_http_flag
        result = verifier(flag_obj, submitted_flag, config)
    else:
        result = _verify_static_flag(flag_obj, submitted_flag)
    return result


def verify_flag(challenge: CTFChallenge, submitted_flag: str) -> bool:
    """Verify a submitted flag against a challenge.

    Checks CTFFlag records first. If none exist, falls back to the legacy
    flag_hash field on the challenge for backward compatibility.

    Args:
        challenge: The challenge to verify against.
        submitted_flag: The flag submitted by the participant.

    Returns:
        True if the flag is correct, False otherwise.
    """
    # Check CTFFlag records first (single query)
    flags = list(challenge.flags.all())
    if flags:
        return any(verify_single_flag(flag_obj, submitted_flag) for flag_obj in flags)

    # Backward compat: fall back to the legacy challenge.flag_hash. A non-hash
    # sentinel ("multi-flag" and similar) means the challenge relies on CTFFlag
    # rows that have all been removed; verifying against it would silently reject
    # every submission with no diagnostic. Log loudly and return False instead of
    # failing quietly so the misconfiguration is visible (#1146).
    legacy_hash = challenge.flag_hash
    if not legacy_hash or not legacy_hash.startswith(("$2", "pbkdf2:", "sha256:")):
        logger.error(
            "Challenge %s has no flag records and no usable legacy flag_hash "
            "(value=%r); every submission will be rejected. Re-add at least one flag.",
            challenge.id,
            legacy_hash,
        )
        return False
    return _verify_hash(submitted_flag, legacy_hash, challenge.id)


def _validate_programmable_config(validator_config: dict[str, Any] | None) -> None:
    """Validate configuration for a programmable flag.

    Args:
        validator_config: The validator configuration dict.

    Raises:
        CTFValidationError: If configuration is invalid.
    """
    from ctf.validators import get_validator

    if validator_config is None or not isinstance(validator_config, dict):
        raise CTFValidationError(
            "validator_config is required for programmable flags",
            details={"missing_fields": ["validator_config"]},
        )
    validator_name = validator_config.get("validator_name", "")
    if not validator_name:
        raise CTFValidationError(
            "validator_config.validator_name is required",
            details={"missing_fields": ["validator_config.validator_name"]},
        )
    if get_validator(validator_name) is None:
        raise CTFValidationError(
            f"Unknown validator: {validator_name}",
            details={"validator_name": validator_name},
        )


def _validate_http_config(validator_config: dict[str, Any] | None) -> None:
    """Validate configuration for an HTTP flag.

    Args:
        validator_config: The validator configuration dict.

    Raises:
        CTFValidationError: If configuration is invalid.
    """
    if validator_config is None or not isinstance(validator_config, dict):
        raise CTFValidationError(
            "validator_config is required for HTTP flags",
            details={"missing_fields": ["validator_config"]},
        )
    url = validator_config.get("url", "")
    if not url:
        raise CTFValidationError(
            "validator_config.url is required",
            details={"missing_fields": ["validator_config.url"]},
        )
    if not url.startswith("https://"):
        raise CTFValidationError(
            "validator_config.url must use HTTPS",
            details={"url": url},
        )

    from ctf.validators import is_blocked_url

    if is_blocked_url(url):
        raise CTFValidationError(
            "validator_config.url must not target private or reserved addresses",
            details={"url": url},
        )

    timeout = validator_config.get("timeout")
    if timeout is not None and (not isinstance(timeout, int) or timeout < 1 or timeout > 30):
        raise CTFValidationError(
            "validator_config.timeout must be an integer between 1 and 30",
            details={"timeout": timeout},
        )


VALID_FLAG_TYPES = ("static", "regex", "programmable", "http")
