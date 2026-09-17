"""Canonical audit evidence validation, hashing, and verification.

The hot audit ledger is a versioned SHA-256 chain.  This module owns the byte
profile so writers, upgrade backfills, and offline verification cannot drift
into subtly different representations.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shared.audit.events import AuditEvent
from shared.audit.vocabulary import AuditAction, AuditActorType, AuditEntityType

if TYPE_CHECKING:
    from shared.models import AuditChainHead, AuditLog

CANONICALIZATION_VERSION = 1
CHAIN_GENERATION = 1
CHAIN_HEAD_SINGLETON = 1
MAX_ENTITY_REF_LENGTH = 255
MAX_CONTEXT_LENGTH = 2_000
MAX_REQUEST_ID_LENGTH = 64
MAX_USER_AGENT_LENGTH = 500
MAX_STATE_BYTES = 65_536
MAX_STATE_DEPTH = 12
MAX_STATE_STRING_LENGTH = 8_192
MAX_POSITIVE_INTEGER = 2_147_483_647

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credentials",
        "password",
        "presigned_url",
        "private_key",
        "secret",
        "signed_url",
        "token",
    }
)
_SECRET_KEY_SUFFIXES = ("_credentials", "_password", "_private_key", "_secret", "_token")


class AuditIntegrityError(RuntimeError):
    """The audit ledger is missing, inconsistent, or cryptographically invalid."""


@dataclass(frozen=True)
class AuditVerificationResult:
    """Bounded successful verification result."""

    record_count: int
    terminal_digest: str
    deployment_scope: str
    chain_generation: int
    canonicalization_version: int


def _validate_scalar_text(name: str, value: object, *, maximum: int, single_line: bool = True) -> str:
    """Validate bounded text and return it with a narrowed type."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    if single_line and ("\n" in value or "\r" in value):
        raise ValueError(f"{name} must be single-line")
    return value


def _validate_json_value(value: object, *, path: str, depth: int) -> None:
    """Validate one value in a bounded, secret-free JSON tree."""
    if depth > MAX_STATE_DEPTH:
        raise ValueError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, bool | int):
        return
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
    elif isinstance(value, str):
        if len(value) > MAX_STATE_STRING_LENGTH:
            raise ValueError(f"{path} contains an oversized string")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
    elif isinstance(value, dict):
        _validate_json_object(value, path=path, depth=depth)
    else:
        raise TypeError(f"{path} contains non-JSON value {type(value).__name__}")


def _validate_json_object(value: dict[object, object], *, path: str, depth: int) -> None:
    """Validate keys and child values in a JSON object."""
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{path} contains a non-string object key")
        normalized_key = key.lower().replace("-", "_")
        is_secret_key = normalized_key in _SECRET_KEYS or normalized_key.endswith(_SECRET_KEY_SUFFIXES)
        if is_secret_key and item not in (None, "", False, [], {}):
            raise ValueError(f"{path} contains prohibited secret-bearing field {key!r}")
        _validate_json_value(item, path=f"{path}.{key}", depth=depth + 1)


def _validate_state(name: str, value: object) -> None:
    """Validate and size one optional audit state object."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object or null")
    _validate_json_value(value, path=name, depth=0)
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError(f"{name} exceeds {MAX_STATE_BYTES} canonical bytes")


def validate_audit_event(event: AuditEvent) -> None:
    """Validate the one accepted shape before persistence and hashing."""
    if event.entity_type not in AuditEntityType.values:
        raise ValueError("entity_type is not active audit vocabulary")
    if event.action not in AuditAction.values:
        raise ValueError("action is not active audit vocabulary")
    if event.actor_type not in AuditActorType.values:
        raise ValueError("actor_type is not active audit vocabulary")
    if not _is_database_integer(event.entity_id):
        raise ValueError("entity_id must fit a non-negative database integer")
    if event.actor_id is not None and not _is_database_integer(event.actor_id):
        raise ValueError("actor_id must fit a non-negative database integer or null")
    _validate_scalar_text("entity_ref", event.entity_ref, maximum=MAX_ENTITY_REF_LENGTH)
    _validate_scalar_text("context", event.context, maximum=MAX_CONTEXT_LENGTH)
    _validate_scalar_text("user_agent", event.user_agent, maximum=MAX_USER_AGENT_LENGTH)
    _validate_scalar_text("request_id", event.request_id, maximum=MAX_REQUEST_ID_LENGTH)
    _normalized_source_ip(event.source_ip)
    _validate_state("previous_state", event.previous_state)
    _validate_state("new_state", event.new_state)


def _is_database_integer(value: object) -> bool:
    """Return whether a value fits the audit table's non-negative integer fields."""
    return not isinstance(value, bool) and isinstance(value, int) and 0 <= value <= MAX_POSITIVE_INTEGER


def _normalized_time(value: datetime) -> str:
    """Render a timezone-aware timestamp in the canonical UTC form."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recorded_at must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalized_source_ip(value: object) -> str | None:
    """Render an optional IPv4 or IPv6 address in compressed form."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("source_ip must be a string or null")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError as exc:
        raise ValueError("source_ip must be a valid IP address") from exc


def canonical_record_payload(record: Mapping[str, Any]) -> dict[str, object]:
    """Return the exact version-1 evidence object."""
    canonicalization_version = int(record["canonicalization_version"])
    if canonicalization_version != CANONICALIZATION_VERSION:
        raise ValueError(f"unsupported canonicalization version {canonicalization_version}")
    return {
        "action": record["action"],
        "actor_id": record["actor_id"],
        "actor_type": record["actor_type"],
        "canonicalization_version": canonicalization_version,
        "chain_generation": record["chain_generation"],
        "context": record["context"],
        "deployment_scope": record["deployment_scope"],
        "entity_id": record["entity_id"],
        "entity_ref": record["entity_ref"],
        "entity_type": record["entity_type"],
        "event_id": str(record["event_id"]),
        "new_state": record["new_state"],
        "previous_digest": record["previous_digest"],
        "previous_state": record["previous_state"],
        "recorded_at": _normalized_time(record["recorded_at"]),
        "request_id": record["request_id"],
        "sequence": record["sequence"],
        "source_ip": _normalized_source_ip(record["source_ip"]),
        "user_agent": record["user_agent"],
    }


def canonical_record_digest(record: Mapping[str, Any]) -> str:
    """Hash one record using the explicit canonical JSON profile."""
    payload = canonical_record_payload(record)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def digest_for_row(row: AuditLog) -> str:
    """Recompute one persisted row's expected digest."""
    return canonical_record_digest(
        {
            "event_id": row.event_id,
            "deployment_scope": row.deployment_scope,
            "chain_generation": row.chain_generation,
            "sequence": row.sequence,
            "canonicalization_version": row.canonicalization_version,
            "recorded_at": row.timestamp,
            "previous_digest": row.previous_digest,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "entity_ref": row.entity_ref,
            "action": row.action,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "previous_state": row.previous_state,
            "new_state": row.new_state,
            "context": row.context,
            "source_ip": row.source_ip,
            "user_agent": row.user_agent,
            "request_id": row.request_id,
        }
    )


def _verify_row(
    row: AuditLog,
    head: AuditChainHead,
    *,
    expected_sequence: int,
    expected_previous: str,
) -> None:
    """Verify one row against its chain position and locked head metadata."""
    if row.sequence != expected_sequence:
        raise AuditIntegrityError(f"expected sequence {expected_sequence}, observed {row.sequence}")
    if row.deployment_scope != head.deployment_scope:
        raise AuditIntegrityError(f"deployment scope mismatch at sequence {row.sequence}")
    if row.chain_generation != head.chain_generation:
        raise AuditIntegrityError(f"chain generation mismatch at sequence {row.sequence}")
    if row.canonicalization_version != head.canonicalization_version:
        raise AuditIntegrityError(f"canonicalization version mismatch at sequence {row.sequence}")
    if row.previous_digest != expected_previous:
        raise AuditIntegrityError(f"predecessor mismatch at sequence {row.sequence}")
    if digest_for_row(row) != row.record_digest:
        raise AuditIntegrityError(f"digest mismatch at sequence {row.sequence}")


def verify_audit_chain() -> AuditVerificationResult:
    """Verify the complete hot ledger without changing any record."""
    from django.db import transaction

    from shared.models import AuditChainHead, AuditLog

    with transaction.atomic():
        try:
            head = AuditChainHead.objects.select_for_update().get(singleton=CHAIN_HEAD_SINGLETON)
        except AuditChainHead.DoesNotExist as exc:
            raise AuditIntegrityError("audit chain head is missing") from exc

        expected_sequence = 1
        expected_previous = ""
        count = 0
        for row in AuditLog.objects.order_by("sequence").iterator():
            _verify_row(
                row,
                head,
                expected_sequence=expected_sequence,
                expected_previous=expected_previous,
            )
            expected_previous = row.record_digest
            expected_sequence += 1
            count += 1

        if head.last_sequence != count:
            raise AuditIntegrityError(f"chain head sequence mismatch: head={head.last_sequence} records={count}")
        if head.last_digest != expected_previous:
            raise AuditIntegrityError("chain head digest mismatch")
        return AuditVerificationResult(
            record_count=count,
            terminal_digest=expected_previous,
            deployment_scope=head.deployment_scope,
            chain_generation=head.chain_generation,
            canonicalization_version=head.canonicalization_version,
        )
