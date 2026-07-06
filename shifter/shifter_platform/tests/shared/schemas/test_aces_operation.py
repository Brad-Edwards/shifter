"""Tests for ACES operation sidecar metadata validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from shared.schemas.aces_operation import (
    AcesOperationRecordData,
    AcesOperationRecordError,
    canonical_aces_payload_digest,
    validate_aces_operation_record,
)

REQUEST_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE_TS = datetime(2026, 7, 5, 3, 0, tzinfo=UTC)


def _record(**overrides):
    fields = {
        "request_id": REQUEST_ID,
        "operation_id": "op-12345678",
        "idempotency_key": "range-provisioned:event-1",
        "record_kind": "operation_receipt",
        "contract_kind": "aces",
        "contract_version": "operation-receipt-v1",
        "contract_profile": "provisioning-only",
        "source_timestamp": SOURCE_TS,
        "payload": {"operation_id": "op-12345678", "accepted": True},
        "diagnostic_refs": {"trace_ref": "traces/request-12345678"},
    }
    fields.update(overrides)
    fields.setdefault("payload_digest", canonical_aces_payload_digest(fields["payload"]))
    return AcesOperationRecordData(**fields)


@pytest.mark.parametrize(
    ("record_kind", "contract_version", "payload"),
    [
        ("operation_receipt", "operation-receipt-v1", {"operation_id": "op-1", "accepted": True}),
        ("operation_status", "operation-status-v1", {"operation_id": "op-1", "status": "running"}),
        ("runtime_snapshot", "runtime-snapshot-v1", {"operation_id": "op-1", "resources": [{"kind": "vm"}]}),
        (
            "execution_plan_ref",
            "execution-plan-ref-v1",
            {"execution_plan_ref": "aces/plans/op-1.json", "execution_plan_digest": "sha256:" + "b" * 64},
        ),
    ],
)
def test_valid_supported_operation_records_pass(record_kind, contract_version, payload):
    result = validate_aces_operation_record(
        _record(record_kind=record_kind, contract_version=contract_version, payload=payload)
    )

    assert result.payload == payload
    assert result.diagnostic_refs == {"trace_ref": "traces/request-12345678"}


def test_unsupported_profile_rejected():
    with pytest.raises(AcesOperationRecordError, match="contract_profile"):
        validate_aces_operation_record(_record(contract_profile="orchestration"))


def test_mismatched_record_kind_and_contract_version_rejected():
    with pytest.raises(AcesOperationRecordError, match="contract_version"):
        validate_aces_operation_record(_record(record_kind="operation_status", contract_version="runtime-snapshot-v1"))


def test_payload_digest_must_match_canonical_payload():
    with pytest.raises(AcesOperationRecordError, match="payload_digest"):
        validate_aces_operation_record(_record(payload_digest="sha256:" + "f" * 64))


def test_secret_bearing_payload_key_rejected():
    with pytest.raises(AcesOperationRecordError, match="secret-bearing"):
        validate_aces_operation_record(_record(payload={"operation_id": "op-1", "access_token": "raw-token"}))


def test_token_bearing_diagnostic_reference_rejected():
    with pytest.raises(AcesOperationRecordError, match="secret-bearing"):
        validate_aces_operation_record(
            _record(diagnostic_refs={"log_ref": "https://example.invalid/log?X-Amz-Signature=abc"})
        )


def test_unknown_diagnostic_reference_key_rejected():
    with pytest.raises(AcesOperationRecordError, match="not an allowed"):
        validate_aces_operation_record(_record(diagnostic_refs={"terraform_output": "s3://bucket/output"}))


def test_execution_plan_reference_rejects_embedded_plan_body():
    with pytest.raises(AcesOperationRecordError, match="reference-only"):
        validate_aces_operation_record(
            _record(
                record_kind="execution_plan_ref",
                contract_version="execution-plan-ref-v1",
                payload={"execution_plan_ref": "aces/plans/op-1.json", "step_refs": ["aces/plans/op-1.step-1.json"]},
            )
        )
