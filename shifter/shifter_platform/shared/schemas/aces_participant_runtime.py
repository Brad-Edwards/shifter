"""Shared-native validation for ACES participant-runtime sidecar records (#1288).

Mirrors :mod:`shared.schemas.aces_operation` (the incumbent
:class:`shared.models.AcesOperationRecord` validation pattern), reusing the
generic primitives extracted to :mod:`shared.schemas._aces_validation`. This
is the first participant-runtime storage slice: it validates
``participant_implementation`` and ``participant_runtime`` sidecar records
before persistence. It does not move lifecycle, access, scoring, or runtime
authority out of existing product tables/services (see
``docs/architecture/aces-participant-runtime-api-sidecars-preflight-1288.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from shared.aces.contracts import (
    PARTICIPANT_RECORD_KIND_TO_CONTRACT_VERSIONS,
    SHIFTER_BACKEND_PROFILE,
    SHIFTER_SUPPORTED_PARTICIPANT_RUNTIME_PROFILES,
)
from shared.schemas._aces_validation import (
    AcesRecordError,
    JsonObject,
    _reject_secret_key,
    _require_aware_datetime,
    _require_digest,
    _require_json_size,
    _require_single_line_ref,
    _require_uuid,
    _validate_json_value,
    canonical_aces_payload_digest,
    validate_diagnostic_refs,
)

PAYLOAD_KEYS_BY_RECORD_KIND = {
    "participant_implementation": frozenset(
        {
            "backend_name",
            "capability_refs",
            "implementation_digest",
            "implementation_ref",
            "participant_ref",
            "request_id",
            "source_timestamp",
            "status",
        }
    ),
    "participant_runtime": frozenset(
        {
            "participant_ref",
            "request_id",
            "runtime_digest",
            "runtime_ref",
            "source_timestamp",
            "status",
            "status_reason",
            "updated_at",
        }
    ),
}
REQUIRED_PAYLOAD_KEYS_BY_RECORD_KIND = {
    "participant_implementation": frozenset({"participant_ref", "implementation_ref"}),
    "participant_runtime": frozenset({"participant_ref", "status"}),
}

#: Component boundaries permitted to own a participant-runtime sidecar write.
OWNER_VALUES = frozenset({"shared", "engine", "provisioner", "cms", "ctf"})

#: Small validated vocabularies; single value today, extensibility seam for
#: later retention/redaction policies (per preflight #1288).
RETENTION_CLASS_VALUES = frozenset({"default"})
REDACTION_STATE_VALUES = frozenset({"sanitized"})

_MAX_JSON_BYTES = 65536
#: Column-aligned caps (see ``shared.models.AcesParticipantRuntimeRecord``): the
#: validator rejects over-length values instead of deferring to a database error.
_MAX_PARTICIPANT_REF_LEN = 256
_MAX_IDEMPOTENCY_KEY_LEN = 128


class AcesParticipantRuntimeRecordError(AcesRecordError):
    """Raised when an ACES participant-runtime sidecar record violates the storage contract."""


@dataclass(frozen=True)
class AcesParticipantRuntimeRecordData:
    """Bundle of sidecar fields validated together at the model/service boundary."""

    request_id: UUID
    participant_ref: str
    idempotency_key: str
    record_kind: str
    contract_kind: str
    contract_version: str
    contract_profile: str
    participant_runtime_profile: str
    source_timestamp: datetime
    payload_digest: str
    payload: object
    diagnostic_refs: object = None
    range_id: UUID | None = None
    range_instance_id: UUID | None = None
    owner: str = "shared"
    retention_class: str = "default"
    redaction_state: str = "sanitized"


@dataclass(frozen=True)
class ValidatedAcesParticipantRuntimeRecord:
    """Normalized JSON fields safe to persist."""

    payload: JsonObject
    diagnostic_refs: JsonObject


def _validate_payload(record_kind: str, payload: object) -> JsonObject:
    """Validate record-kind-specific sidecar payload shape and contents."""
    if not isinstance(payload, dict):
        raise AcesParticipantRuntimeRecordError("payload must be a JSON object")
    for key in payload:
        if not isinstance(key, str):
            raise AcesParticipantRuntimeRecordError("payload keys must be strings")
        _reject_secret_key("payload", key, error_cls=AcesParticipantRuntimeRecordError)
    allowed_keys = PAYLOAD_KEYS_BY_RECORD_KIND[record_kind]
    extra_keys = set(payload) - allowed_keys
    if extra_keys:
        raise AcesParticipantRuntimeRecordError(
            f"payload keys {sorted(extra_keys)} are not allowed for record_kind {record_kind!r}"
        )
    missing_keys = REQUIRED_PAYLOAD_KEYS_BY_RECORD_KIND[record_kind] - set(payload)
    if missing_keys:
        raise AcesParticipantRuntimeRecordError(
            f"payload keys {sorted(missing_keys)} are required for record_kind {record_kind!r}"
        )
    _require_json_size("payload", payload, max_bytes=_MAX_JSON_BYTES, error_cls=AcesParticipantRuntimeRecordError)
    validated = _validate_json_value("payload", payload, error_cls=AcesParticipantRuntimeRecordError)
    if not isinstance(validated, dict):
        raise AcesParticipantRuntimeRecordError("payload must be a JSON object")
    return validated


def _validate_record_kind_contract(record_kind: str, contract_version: str) -> None:
    """Validate that a record kind and contract version are compatible."""
    versions = PARTICIPANT_RECORD_KIND_TO_CONTRACT_VERSIONS.get(record_kind)
    if versions is None:
        raise AcesParticipantRuntimeRecordError(
            f"record_kind must be one of {sorted(PARTICIPANT_RECORD_KIND_TO_CONTRACT_VERSIONS)}"
        )
    if contract_version not in versions:
        raise AcesParticipantRuntimeRecordError(
            f"contract_version {contract_version!r} is not valid for record_kind {record_kind!r}"
        )


def validate_aces_participant_runtime_record(
    record: AcesParticipantRuntimeRecordData,
) -> ValidatedAcesParticipantRuntimeRecord:
    """Validate an ACES participant-runtime sidecar record before persistence."""

    _require_uuid("request_id", record.request_id, required=True, error_cls=AcesParticipantRuntimeRecordError)
    _require_uuid("range_id", record.range_id, required=False, error_cls=AcesParticipantRuntimeRecordError)
    _require_uuid(
        "range_instance_id", record.range_instance_id, required=False, error_cls=AcesParticipantRuntimeRecordError
    )
    _require_single_line_ref(
        "participant_ref",
        record.participant_ref,
        required=True,
        max_len=_MAX_PARTICIPANT_REF_LEN,
        error_cls=AcesParticipantRuntimeRecordError,
    )
    _require_single_line_ref(
        "idempotency_key",
        record.idempotency_key,
        required=True,
        max_len=_MAX_IDEMPOTENCY_KEY_LEN,
        error_cls=AcesParticipantRuntimeRecordError,
    )
    if record.contract_kind != "aces":
        raise AcesParticipantRuntimeRecordError("contract_kind must be 'aces'")
    if record.contract_profile != SHIFTER_BACKEND_PROFILE:
        raise AcesParticipantRuntimeRecordError(f"contract_profile must be {SHIFTER_BACKEND_PROFILE!r}")
    if record.participant_runtime_profile not in SHIFTER_SUPPORTED_PARTICIPANT_RUNTIME_PROFILES:
        raise AcesParticipantRuntimeRecordError(
            f"participant_runtime_profile must be one of {sorted(SHIFTER_SUPPORTED_PARTICIPANT_RUNTIME_PROFILES)}"
        )
    _validate_record_kind_contract(record.record_kind, record.contract_version)
    if record.owner not in OWNER_VALUES:
        raise AcesParticipantRuntimeRecordError(f"owner must be one of {sorted(OWNER_VALUES)}")
    if record.retention_class not in RETENTION_CLASS_VALUES:
        raise AcesParticipantRuntimeRecordError(f"retention_class must be one of {sorted(RETENTION_CLASS_VALUES)}")
    if record.redaction_state not in REDACTION_STATE_VALUES:
        raise AcesParticipantRuntimeRecordError(f"redaction_state must be one of {sorted(REDACTION_STATE_VALUES)}")
    _require_aware_datetime("source_timestamp", record.source_timestamp, error_cls=AcesParticipantRuntimeRecordError)
    _require_digest("payload_digest", record.payload_digest, error_cls=AcesParticipantRuntimeRecordError)
    payload = _validate_payload(record.record_kind, record.payload)
    expected_digest = canonical_aces_payload_digest(payload, error_cls=AcesParticipantRuntimeRecordError)
    if record.payload_digest != expected_digest:
        raise AcesParticipantRuntimeRecordError("payload_digest must match canonical payload digest")
    return ValidatedAcesParticipantRuntimeRecord(
        payload=payload,
        diagnostic_refs=validate_diagnostic_refs(record.diagnostic_refs, error_cls=AcesParticipantRuntimeRecordError),
    )
