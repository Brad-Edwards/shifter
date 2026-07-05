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
