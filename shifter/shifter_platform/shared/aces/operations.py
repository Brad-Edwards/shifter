"""Persistence helpers for ACES operation sidecar records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from shared.aces.contracts import SHIFTER_BACKEND_PROFILE
from shared.models import AcesOperationRecord
from shared.schemas.aces_operation import canonical_aces_payload_digest


class AcesOperationRecordConflict(ValueError):
    """Raised when an idempotent replay carries conflicting operation content."""


@dataclass(frozen=True)
class AcesOperationRecordWrite:
    """Input contract for writing one ACES operation sidecar record."""

    request_id: UUID | str
    operation_id: str
    idempotency_key: str
    record_kind: str
    contract_version: str
    source_timestamp: datetime
    payload: dict[str, Any]
    payload_digest: str | None = None
    diagnostic_refs: dict[str, Any] | None = None
    range_id: UUID | str | None = None
    contract_kind: str = AcesOperationRecord.ContractKind.ACES
    contract_profile: str = SHIFTER_BACKEND_PROFILE
    owner: str = AcesOperationRecord.Owner.SHARED
    retention_expires_at: datetime | None = None


def _idempotency_lookup(write: AcesOperationRecordWrite) -> dict[str, Any]:
    """Build the database lookup tuple that defines replay identity."""
    return {
        "request_id": write.request_id,
        "record_kind": write.record_kind,
        "contract_version": write.contract_version,
        "contract_profile": write.contract_profile,
        "idempotency_key": write.idempotency_key,
    }


def _assert_replay_matches(
    existing: AcesOperationRecord,
    *,
    write: AcesOperationRecordWrite,
    payload_digest: str,
) -> None:
    """Reject idempotency-key reuse with different canonical content."""
    if (
        existing.operation_id != write.operation_id
        or existing.source_timestamp != write.source_timestamp
        or existing.payload_digest != payload_digest
    ):
        raise AcesOperationRecordConflict(
            "idempotency conflict: replay has different operation_id, source_timestamp, or payload_digest"
        )


def persist_aces_operation_record(write: AcesOperationRecordWrite) -> AcesOperationRecord:
    """Create or return an idempotent ACES operation sidecar record.

    The database enforces the replay key. The service enforces deterministic
    replay semantics by rejecting drift in the canonical content identity.
    """
    normalized_payload_digest = write.payload_digest or canonical_aces_payload_digest(write.payload)
    lookup = _idempotency_lookup(write)
    defaults = {
        "range_id": write.range_id,
        "operation_id": write.operation_id,
        "contract_kind": write.contract_kind,
        "source_timestamp": write.source_timestamp,
        "payload_digest": normalized_payload_digest,
        "payload": write.payload,
        "diagnostic_refs": write.diagnostic_refs or {},
        "owner": write.owner,
        "retention_expires_at": write.retention_expires_at,
    }

    try:
        with transaction.atomic():
            record, created = AcesOperationRecord.objects.get_or_create(**lookup, defaults=defaults)
    except IntegrityError:
        record = AcesOperationRecord.objects.get(**lookup)
        created = False

    if not created:
        _assert_replay_matches(
            record,
            write=write,
            payload_digest=normalized_payload_digest,
        )
    return record


#: Contract version for ``operation-status-v1`` sidecar records.
OPERATION_STATUS_CONTRACT_VERSION = "operation-status-v1"


def latest_operation_status_source_timestamp(request_id: UUID | str) -> datetime | None:
    """Return the source timestamp of the latest recorded operation status.

    This is the staleness anchor for status projection (#1274): callers outside
    the ``shared`` layer read it through this seam instead of importing the
    ``AcesOperationRecord`` model directly.
    """
    return (
        AcesOperationRecord.objects.filter(
            request_id=request_id,
            record_kind=AcesOperationRecord.RecordKind.OPERATION_STATUS,
        )
        .order_by("-source_timestamp", "-created_at")
        .values_list("source_timestamp", flat=True)
        .first()
    )


def persist_operation_status_record(
    *,
    request_id: UUID | str,
    operation_id: str,
    source_timestamp: datetime,
    payload: dict[str, Any],
    diagnostic_refs: dict[str, Any] | None = None,
    contract_version: str = OPERATION_STATUS_CONTRACT_VERSION,
    owner: str = AcesOperationRecord.Owner.ENGINE,
) -> AcesOperationRecord:
    """Persist one ``operation_status`` sidecar record idempotently.

    Encapsulates the ``record_kind`` and idempotency-key convention for
    operation-status observations so callers outside ``shared`` do not touch the
    ``AcesOperationRecord`` model. The idempotency key is deterministic in the
    operation id and observation timestamp, so re-delivery of the same
    observation is a no-op (or a conflict when the content drifts).
    """
    write = AcesOperationRecordWrite(
        request_id=request_id,
        operation_id=operation_id,
        idempotency_key=f"operation_status:{operation_id}:{source_timestamp.isoformat()}",
        record_kind=AcesOperationRecord.RecordKind.OPERATION_STATUS,
        contract_version=contract_version,
        source_timestamp=source_timestamp,
        payload=payload,
        diagnostic_refs=diagnostic_refs or {},
        owner=owner,
    )
    return persist_aces_operation_record(write)
