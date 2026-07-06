"""Shared-native validation for ACES operation sidecar records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from shared.aces.contracts import (
    OPERATION_RECORD_KIND_TO_CONTRACT_VERSIONS,
    SHIFTER_BACKEND_PROFILE,
)

Scalar = str | int | float | bool | None
JsonValue = Scalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]

DIAGNOSTIC_REF_KEYS = frozenset(
    {
        "artifact_ref",
        "error_class",
        "fingerprint",
        "log_ref",
        "provider_ref",
        "report_ref",
        "source_event_id",
        "status_reason",
        "trace_ref",
    }
)
EXECUTION_PLAN_REF_KEYS = frozenset(
    {
        "execution_plan_ref",
        "execution_plan_digest",
        "generated_at",
        "notes",
        "tool",
        "tool_version",
    }
)
PAYLOAD_KEYS_BY_RECORD_KIND = {
    "operation_receipt": frozenset(
        {
            "accepted",
            "diagnostic_refs",
            "operation_id",
            "receipt_digest",
            "receipt_ref",
            "request_id",
            "source_timestamp",
            "status",
        }
    ),
    "operation_status": frozenset(
        {
            "diagnostic_refs",
            "operation_id",
            "request_id",
            "source_timestamp",
            "status",
            "status_reason",
            "updated_at",
        }
    ),
    "runtime_snapshot": frozenset(
        {
            "captured_at",
            "diagnostic_refs",
            "operation_id",
            "request_id",
            "resources",
            "snapshot_digest",
            "snapshot_ref",
            "status",
        }
    ),
    "execution_plan_ref": EXECUTION_PLAN_REF_KEYS,
}
REQUIRED_PAYLOAD_KEYS_BY_RECORD_KIND = {
    "operation_receipt": frozenset({"operation_id"}),
    "operation_status": frozenset({"operation_id", "status"}),
    "runtime_snapshot": frozenset({"operation_id", "resources"}),
    "execution_plan_ref": frozenset({"execution_plan_ref"}),
}
OWNER_VALUES = frozenset({"shared", "engine", "provisioner", "cms"})

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REF_LEN = 512
_MAX_SCALAR_LEN = 2048
_MAX_JSON_BYTES = 65536
_MAX_DIAGNOSTIC_BYTES = 4096
_MAX_DIAGNOSTIC_LIST_LEN = 32
_FORBIDDEN_CHARS = ("\n", "\r", "\x00")
_SECRET_KEY_FRAGMENTS = (
    "access_key",
    "api_key",
    "bearer",
    "command",
    "credential",
    "flag",
    "package_body",
    "password",
    "private_key",
    "prompt",
    "provider_dump",
    "raw_payload",
    "script",
    "secret",
    "ssm_output",
    "ssh_output",
    "terraform_output",
    "token",
    "transcript",
)
_SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|bearer\s+|x-amz-signature=|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16})",
    re.IGNORECASE,
)


class AcesOperationRecordError(ValueError):
    """Raised when an ACES operation sidecar record violates the storage contract."""


@dataclass(frozen=True)
class AcesOperationRecordData:
    """Bundle of sidecar fields validated together at the model/service boundary."""

    request_id: UUID
    operation_id: str
    idempotency_key: str
    record_kind: str
    contract_kind: str
    contract_version: str
    contract_profile: str
    source_timestamp: datetime
    payload_digest: str
    payload: object
    diagnostic_refs: object = None
    range_id: UUID | None = None
    owner: str = "shared"


@dataclass(frozen=True)
class ValidatedAcesOperationRecord:
    """Normalized JSON fields safe to persist."""

    payload: JsonObject
    diagnostic_refs: JsonObject


def canonical_aces_payload_digest(payload: object) -> str:
    """Return the deterministic digest used for stored sidecar payloads."""
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcesOperationRecordError("payload must be JSON-serializable") from exc
    return "sha256:" + sha256(encoded).hexdigest()


def _reject_secret_key(path: str, key: str) -> None:
    """Reject field names that indicate embedded secrets or raw provider data."""
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
        raise AcesOperationRecordError(f"secret-bearing payload field rejected at {path}.{key}")


def _reject_secret_value(path: str, value: str) -> None:
    """Reject string values that match high-confidence secret patterns."""
    if _SECRET_VALUE_RE.search(value):
        raise AcesOperationRecordError(f"secret-bearing payload value rejected at {path}")


def _require_single_line_ref(name: str, value: object, *, required: bool) -> str:
    """Normalize bounded reference strings and reject embedded content."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise AcesOperationRecordError(f"{name} must be a string")
    if not value.strip():
        if required:
            raise AcesOperationRecordError(f"{name} is required")
        return ""
    if len(value) > _MAX_REF_LEN:
        raise AcesOperationRecordError(f"{name} exceeds {_MAX_REF_LEN} characters")
    if any(ch in value for ch in _FORBIDDEN_CHARS):
        raise AcesOperationRecordError(f"{name} must be single-line, not embedded content")
    _reject_secret_value(name, value)
    return value


def _require_digest(name: str, value: object) -> str:
    """Validate a canonical sha256 digest string."""
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise AcesOperationRecordError(f"{name} must be a 'sha256:<64 hex>' digest")
    return value


def _require_uuid(name: str, value: object, *, required: bool) -> UUID | None:
    """Validate or coerce UUID fields while preserving optional projections."""
    if value in (None, ""):
        if required:
            raise AcesOperationRecordError(f"{name} is required")
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AcesOperationRecordError(f"{name} must be a UUID") from exc


def _require_aware_datetime(name: str, value: object) -> datetime:
    """Require timezone-aware timestamps for replay comparison."""
    if not isinstance(value, datetime):
        raise AcesOperationRecordError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AcesOperationRecordError(f"{name} must be timezone-aware")
    return value


def _require_json_size(name: str, value: object, *, max_bytes: int) -> None:
    """Enforce a canonical byte budget before recursive JSON validation."""
    try:
        encoded = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise AcesOperationRecordError(f"{name} must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise AcesOperationRecordError(f"{name} exceeds {max_bytes} bytes")


def _validate_json_value(path: str, value: object) -> JsonValue:
    """Recursively validate JSON values against the sidecar safety rules."""
    validated: JsonValue
    if isinstance(value, dict):
        validated = _validate_json_object(path, value)
    elif isinstance(value, list):
        validated = [_validate_json_value(f"{path}[{index}]", item) for index, item in enumerate(value)]
    else:
        validated = _validate_json_scalar(path, value)
    return validated


def _validate_json_scalar(path: str, value: object) -> Scalar:
    """Validate one scalar JSON value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_SCALAR_LEN:
            raise AcesOperationRecordError(f"{path} exceeds {_MAX_SCALAR_LEN} characters")
        if any(ch in value for ch in _FORBIDDEN_CHARS):
            raise AcesOperationRecordError(f"{path} must be single-line, not embedded content")
        _reject_secret_value(path, value)
        return value
    raise AcesOperationRecordError(f"{path} must contain JSON-compatible values")


def _validate_json_object(path: str, value: dict[object, object]) -> JsonObject:
    """Validate a JSON object and its recursively nested fields."""
    validated: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise AcesOperationRecordError(f"{path} keys must be strings")
        _reject_secret_key(path, key)
        validated[key] = _validate_json_value(f"{path}.{key}", item)
    return validated


def _validate_payload(record_kind: str, payload: object) -> JsonObject:
    """Validate record-kind-specific sidecar payload shape and contents."""
    if not isinstance(payload, dict):
        raise AcesOperationRecordError("payload must be a JSON object")
    for key in payload:
        if not isinstance(key, str):
            raise AcesOperationRecordError("payload keys must be strings")
        _reject_secret_key("payload", key)
    allowed_keys = PAYLOAD_KEYS_BY_RECORD_KIND[record_kind]
    extra_keys = set(payload) - allowed_keys
    if extra_keys:
        if record_kind == "execution_plan_ref":
            raise AcesOperationRecordError("execution_plan_ref payload is reference-only")
        raise AcesOperationRecordError(
            f"payload keys {sorted(extra_keys)} are not allowed for record_kind {record_kind!r}"
        )
    missing_keys = REQUIRED_PAYLOAD_KEYS_BY_RECORD_KIND[record_kind] - set(payload)
    if missing_keys:
        raise AcesOperationRecordError(
            f"payload keys {sorted(missing_keys)} are required for record_kind {record_kind!r}"
        )
    _require_json_size("payload", payload, max_bytes=_MAX_JSON_BYTES)
    validated = _validate_json_value("payload", payload)
    if not isinstance(validated, dict):
        raise AcesOperationRecordError("payload must be a JSON object")
    if record_kind == "execution_plan_ref":
        _require_single_line_ref("payload.execution_plan_ref", validated.get("execution_plan_ref"), required=True)
        if "execution_plan_digest" in validated:
            _require_digest("payload.execution_plan_digest", validated["execution_plan_digest"])
    return validated


def _validate_diagnostic_refs(diagnostic_refs: object) -> JsonObject:
    """Validate bounded diagnostic references without accepting raw logs."""
    if diagnostic_refs is None:
        return {}
    if not isinstance(diagnostic_refs, dict):
        raise AcesOperationRecordError("diagnostic_refs must be a JSON object")
    _require_json_size("diagnostic_refs", diagnostic_refs, max_bytes=_MAX_DIAGNOSTIC_BYTES)
    validated: JsonObject = {}
    for key, value in diagnostic_refs.items():
        if not isinstance(key, str):
            raise AcesOperationRecordError("diagnostic_refs keys must be strings")
        if key not in DIAGNOSTIC_REF_KEYS:
            raise AcesOperationRecordError(f"diagnostic_refs key '{key}' is not an allowed reference key")
        if isinstance(value, list):
            if len(value) > _MAX_DIAGNOSTIC_LIST_LEN:
                raise AcesOperationRecordError(f"diagnostic_refs '{key}' list exceeds {_MAX_DIAGNOSTIC_LIST_LEN} items")
            validated[key] = [
                _require_single_line_ref(f"diagnostic_refs.{key}", item, required=False) for item in value
            ]
            continue
        validated[key] = _require_single_line_ref(f"diagnostic_refs.{key}", value, required=False)
    return validated


def _validate_record_kind_contract(record_kind: str, contract_version: str) -> None:
    """Validate that a record kind and contract version are compatible."""
    versions = OPERATION_RECORD_KIND_TO_CONTRACT_VERSIONS.get(record_kind)
    if versions is None:
        raise AcesOperationRecordError(
            f"record_kind must be one of {sorted(OPERATION_RECORD_KIND_TO_CONTRACT_VERSIONS)}"
        )
    if contract_version not in versions:
        raise AcesOperationRecordError(
            f"contract_version {contract_version!r} is not valid for record_kind {record_kind!r}"
        )


def validate_aces_operation_record(record: AcesOperationRecordData) -> ValidatedAcesOperationRecord:
    """Validate an ACES operation sidecar record before persistence."""

    _require_uuid("request_id", record.request_id, required=True)
    _require_uuid("range_id", record.range_id, required=False)
    _require_single_line_ref("operation_id", record.operation_id, required=True)
    _require_single_line_ref("idempotency_key", record.idempotency_key, required=True)
    if record.contract_kind != "aces":
        raise AcesOperationRecordError("contract_kind must be 'aces'")
    if record.contract_profile != SHIFTER_BACKEND_PROFILE:
        raise AcesOperationRecordError(f"contract_profile must be {SHIFTER_BACKEND_PROFILE!r}")
    _validate_record_kind_contract(record.record_kind, record.contract_version)
    if record.owner not in OWNER_VALUES:
        raise AcesOperationRecordError(f"owner must be one of {sorted(OWNER_VALUES)}")
    _require_aware_datetime("source_timestamp", record.source_timestamp)
    _require_digest("payload_digest", record.payload_digest)
    payload = _validate_payload(record.record_kind, record.payload)
    expected_digest = canonical_aces_payload_digest(payload)
    if record.payload_digest != expected_digest:
        raise AcesOperationRecordError("payload_digest must match canonical payload digest")
    return ValidatedAcesOperationRecord(
        payload=payload,
        diagnostic_refs=_validate_diagnostic_refs(record.diagnostic_refs),
    )
