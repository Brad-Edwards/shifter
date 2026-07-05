"""Persistence helpers for ACES operation sidecar records."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from shared.aces.contracts import SHIFTER_BACKEND_PROFILE
from shared.models import AcesOperationRecord
from shared.schemas.aces_operation import canonical_aces_payload_digest


class AcesOperationRecordConflict(ValueError):
    """Raised when an idempotent replay carries conflicting operation content."""


def _idempotency_lookup(
    *,
    request_id: UUID | str,
    record_kind: str,
    contract_version: str,
    contract_profile: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "record_kind": record_kind,
        "contract_version": contract_version,
        "contract_profile": contract_profile,
        "idempotency_key": idempotency_key,
    }


def _assert_replay_matches(
    existing: AcesOperationRecord,
    *,
    operation_id: str,
    source_timestamp: datetime,
    payload_digest: str,
) -> None:
    if (
        existing.operation_id != operation_id
        or existing.source_timestamp != source_timestamp
        or existing.payload_digest != payload_digest
    ):
        raise AcesOperationRecordConflict(
            "idempotency conflict: replay has different operation_id, source_timestamp, or payload_digest"
        )


def persist_aces_operation_record(
    *,
    request_id: UUID | str,
    operation_id: str,
    idempotency_key: str,
    record_kind: str,
    contract_version: str,
    source_timestamp: datetime,
    payload: dict[str, Any],
    payload_digest: str | None = None,
    diagnostic_refs: dict[str, Any] | None = None,
    range_id: UUID | str | None = None,
    contract_kind: str = AcesOperationRecord.ContractKind.ACES,
    contract_profile: str = SHIFTER_BACKEND_PROFILE,
    owner: str = AcesOperationRecord.Owner.SHARED,
    retention_expires_at: datetime | None = None,
) -> AcesOperationRecord:
    """Create or return an idempotent ACES operation sidecar record.

    The database enforces the replay key. The service enforces deterministic
    replay semantics by rejecting drift in the canonical content identity.
    """
    normalized_payload_digest = payload_digest or canonical_aces_payload_digest(payload)
    lookup = _idempotency_lookup(
        request_id=request_id,
        record_kind=record_kind,
        contract_version=contract_version,
        contract_profile=contract_profile,
        idempotency_key=idempotency_key,
    )
    defaults = {
        "range_id": range_id,
        "operation_id": operation_id,
        "contract_kind": contract_kind,
        "source_timestamp": source_timestamp,
        "payload_digest": normalized_payload_digest,
        "payload": payload,
        "diagnostic_refs": diagnostic_refs or {},
        "owner": owner,
        "retention_expires_at": retention_expires_at,
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
            operation_id=operation_id,
            source_timestamp=source_timestamp,
            payload_digest=normalized_payload_digest,
        )
    return record
