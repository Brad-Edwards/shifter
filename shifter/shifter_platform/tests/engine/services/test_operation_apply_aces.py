"""Authoritative apply for ACES operation results (ADR-043 phase 5, #1837).

Phase 4 made the applier authoritative for pause/resume + NGFW. This suite
covers what phase 5 adds: ``aces-range`` provision/destroy results drive the
Range lifecycle, persist the ACES sidecar evidence, write strict audit, and
enqueue the ADR-025 notification -- all in the applier's one transaction, with
snapshots deliberately excluded from audit and notification.

The pre-cutover path reached the same sidecar records through
``range.aces.operation`` / ``range.aces.snapshot`` outbox events. These tests
drive the result inbox instead, which is the authoritative seam.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from engine.models import OperationResultDisposition, OperationResultInbox, Range, RangeEventOutbox, Request
from engine.services import apply_pending_operation_results
from shared.aces.status import ACES_STATE_RUNNING, ACES_STATE_SUCCEEDED
from shared.audit import bind_audit_writer, get_audit_writer, reset_audit_writer
from shared.enums import ResourceStatus
from shared.models import AcesOperationRecord
from shared.operation_envelope import build_operation_envelope, canonical_payload_digest
from shared.operation_results import ResultStep, build_result_identity, result_kind_for

# Opaque #1325 workspace scope binding (ADR-046-R3). These suites do not
# exercise tenancy; a fixed scalar stands in for the value the CMS launch
# facade resolves in production.
_WORKSPACE_ID = 1

pytestmark = pytest.mark.django_db


class _Fixture:
    """An ACES range owning a live operation generation."""

    def __init__(self, *, operation: str = "provision", status: str = ResourceStatus.PENDING.value):
        self.operation = operation
        self.operation_id = uuid4()
        self.request_id = uuid4()
        self.user = get_user_model().objects.create_user(username=f"{self.request_id}@example.com")
        self.request = Request.objects.create(request_id=self.request_id, request_type="range", user=self.user)
        self.range = Range.objects.create(
            workspace_id=_WORKSPACE_ID,
            request=self.request,
            user=self.user,
            status=status,
            provisioner_operation_id=self.operation_id,
        )

    def seed(
        self,
        step: ResultStep,
        payload: dict,
        *,
        operation_id=None,
        resource: str = "aces-range",
    ) -> OperationResultInbox:
        operation_id = operation_id or self.operation_id
        envelope = build_operation_envelope(
            operation_id=operation_id,
            request_id=self.request_id,
            resource=resource,
            operation=self.operation,
            payload=payload,
        )
        digest = canonical_payload_digest(envelope["payload"])
        return OperationResultInbox.objects.create(
            operation_id=operation_id,
            request_id=self.request_id,
            resource=resource,
            operation=self.operation,
            contract_version="1",
            result_kind=result_kind_for(resource, self.operation, step=step),
            result_step=step,
            result_identity=build_result_identity(operation_id=operation_id, step=step, digest=digest),
            payload_digest=digest,
            envelope=envelope,
        )


def _disposition(row: OperationResultInbox) -> str:
    row.refresh_from_db()
    return row.disposition


def _snapshot(count: int = 2) -> dict:
    return {
        "resources": [
            {"address": f"node.n{index}", "resource_type": "node", "status": "provisioned"} for index in range(count)
        ]
    }


class TestProvisionLifecycle:
    def test_running_moves_the_range_to_provisioning_and_records_evidence(self):
        fx = _Fixture()
        row = fx.seed(ResultStep.ACES_PROVISION_RUNNING, {"aces_status": ACES_STATE_RUNNING})

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.PROVISIONING.value
        assert AcesOperationRecord.objects.filter(request_id=fx.request_id, operation_id=str(fx.operation_id)).exists()

    def test_terminal_success_moves_the_range_to_ready_and_notifies(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        row = fx.seed(ResultStep.ACES_TERMINAL_READY, {"aces_status": ACES_STATE_SUCCEEDED})

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.READY.value
        assert fx.range.ready_at is not None
        assert RangeEventOutbox.objects.count() == 1

    def test_evidence_carries_the_canonical_generation_not_the_request(self):
        # Historical sidecar rows used request_id as the operation id. New
        # results must carry the ADR-043 generation, or replay/fencing keys on
        # the wrong identity.
        fx = _Fixture()
        fx.seed(ResultStep.ACES_PROVISION_RUNNING, {"aces_status": ACES_STATE_RUNNING})

        apply_pending_operation_results()

        record = AcesOperationRecord.objects.get(request_id=fx.request_id)
        assert record.operation_id == str(fx.operation_id)
        assert record.operation_id != str(fx.request_id)


class TestSnapshotIsEvidenceOnly:
    def test_snapshot_persists_a_record_without_touching_lifecycle(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        row = fx.seed(ResultStep.ACES_PROVISION_SNAPSHOT, _snapshot())

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.PROVISIONING.value
        assert AcesOperationRecord.objects.filter(request_id=fx.request_id).count() == 1

    def test_snapshot_enqueues_no_range_event(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        fx.seed(ResultStep.ACES_PROVISION_SNAPSHOT, _snapshot())

        apply_pending_operation_results()

        assert RangeEventOutbox.objects.count() == 0


class TestDestroyLifecycle:
    def test_running_records_evidence_without_a_status_write(self):
        fx = _Fixture(operation="destroy", status=ResourceStatus.DESTROYING.value)
        row = fx.seed(ResultStep.ACES_DESTROY_RUNNING, {"aces_status": ACES_STATE_RUNNING})

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.DESTROYING.value
        assert RangeEventOutbox.objects.count() == 0

    def test_terminal_destroyed_moves_the_range_and_notifies(self):
        fx = _Fixture(operation="destroy", status=ResourceStatus.DESTROYING.value)
        row = fx.seed(ResultStep.ACES_TERMINAL_DESTROYED, {"aces_status": ACES_STATE_SUCCEEDED})

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.DESTROYED.value
        assert RangeEventOutbox.objects.count() == 1


class TestFailure:
    def test_failure_records_only_the_authored_reason_code(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        row = fx.seed(
            ResultStep.ACES_TERMINAL_FAILED,
            {"reason_code": "cloud_operation_failed", "diagnostic": "gce insert returned 409 for node.web"},
        )

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.APPLIED
        assert fx.range.status == ResourceStatus.FAILED.value
        # The bounded diagnostic stays in the result payload; only the closed
        # reason code reaches user-visible range error text.
        assert fx.range.error_message == "cloud_operation_failed"
        assert "409" not in (fx.range.error_message or "")

    def test_failure_clears_the_generation(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        fx.seed(ResultStep.ACES_TERMINAL_FAILED, {"reason_code": "cloud_timeout", "diagnostic": ""})

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert fx.range.provisioner_operation_id is None


class TestFencingAndOwnership:
    def test_a_stale_generation_is_refused(self):
        fx = _Fixture()
        row = fx.seed(ResultStep.ACES_PROVISION_RUNNING, {"aces_status": ACES_STATE_RUNNING})
        Range.objects.filter(pk=fx.range.pk).update(provisioner_operation_id=uuid4())

        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(row) == OperationResultDisposition.REJECTED_STALE
        assert fx.range.status == ResourceStatus.PENDING.value
        assert not AcesOperationRecord.objects.filter(request_id=fx.request_id).exists()

    def test_a_result_for_another_request_is_refused(self):
        fx = _Fixture()
        other = _Fixture()
        row = fx.seed(ResultStep.ACES_PROVISION_RUNNING, {"aces_status": ACES_STATE_RUNNING})
        OperationResultInbox.objects.filter(pk=row.pk).update(request_id=other.request_id)

        apply_pending_operation_results()

        assert _disposition(row) in {
            OperationResultDisposition.REJECTED_OWNERSHIP,
            OperationResultDisposition.REJECTED_INVALID,
        }
        assert not AcesOperationRecord.objects.filter(request_id=fx.request_id).exists()

    def test_late_progress_after_a_terminal_is_refused(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        fx.seed(ResultStep.ACES_TERMINAL_READY, {"aces_status": ACES_STATE_SUCCEEDED})
        apply_pending_operation_results()

        late = fx.seed(ResultStep.ACES_PROVISION_RUNNING, {"aces_status": ACES_STATE_RUNNING})
        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert _disposition(late) == OperationResultDisposition.REJECTED_ORDERING
        assert fx.range.status == ResourceStatus.READY.value

    def test_a_conflicting_sibling_is_refused(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        fx.seed(ResultStep.ACES_PROVISION_SNAPSHOT, _snapshot(1))
        second = fx.seed(ResultStep.ACES_PROVISION_SNAPSHOT, _snapshot(2))

        apply_pending_operation_results()

        assert _disposition(second) == OperationResultDisposition.REJECTED_CONFLICT


class TestTransactionIntegrity:
    def test_an_audit_failure_rolls_back_the_whole_result(self):
        # ADR-043-R3: the audit row is the control, so best-effort auditing is
        # not sufficient. A failed audit must leave nothing half-applied.
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        row = fx.seed(ResultStep.ACES_TERMINAL_READY, {"aces_status": ACES_STATE_SUCCEEDED})

        class _FailingAuditWriter:
            def write(self, event) -> None:
                raise RuntimeError("audit writer down")

        # Binding fails closed over an existing writer, so clear the startup
        # binding first and restore it afterwards.
        original = get_audit_writer()
        reset_audit_writer()
        bind_audit_writer(_FailingAuditWriter())
        try:
            with pytest.raises(RuntimeError, match="audit writer down"):
                apply_pending_operation_results()
        finally:
            reset_audit_writer()
            bind_audit_writer(original)

        fx.range.refresh_from_db()
        assert fx.range.status == ResourceStatus.PROVISIONING.value
        assert _disposition(row) == OperationResultDisposition.PENDING
        assert not AcesOperationRecord.objects.filter(request_id=fx.request_id).exists()

    def test_replaying_a_terminal_result_is_idempotent(self):
        fx = _Fixture(status=ResourceStatus.PROVISIONING.value)
        fx.seed(ResultStep.ACES_TERMINAL_READY, {"aces_status": ACES_STATE_SUCCEEDED})
        apply_pending_operation_results()
        first_events = RangeEventOutbox.objects.count()

        # An identical replay collapses on result_identity at the append
        # boundary, so re-running the applier must not double-write.
        apply_pending_operation_results()

        fx.range.refresh_from_db()
        assert fx.range.status == ResourceStatus.READY.value
        assert RangeEventOutbox.objects.count() == first_events
        assert AcesOperationRecord.objects.filter(request_id=fx.request_id).count() == 1


@pytest.mark.postgres
@pytest.mark.django_db
class TestPhase5EffectivePrivileges:
    """Prove the phase-5 revokes against real PostgreSQL (ADR-043-R1).

    A migration emitting a REVOKE string is not evidence; effective privilege
    is. Two-sided on purpose: what the ACES cutover removed must be gone, and
    the grants the uncut cyberscript/NGFW families still depend on must survive,
    so an over-broad revoke fails here rather than in production.
    """

    def _table(self, table: str, priv: str) -> bool:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT has_table_privilege('provisioner_lambda', %s, %s)", [table, priv])
            return bool(cursor.fetchone()[0])

    def test_aces_delivery_binding_read_is_revoked(self):
        # Bindings now ride the operation input; the binding table read is gone.
        assert self._table("engine_aces_content_delivery_binding", "SELECT") is False

    def test_aces_image_registry_read_is_absent(self):
        # Never granted by migration 0027, and must not have arrived by any
        # other route (role inheritance, a schema-wide grant, default
        # privileges). Asserting effective privilege catches all of those; a
        # grep for a GRANT statement would not.
        assert self._table("engine_aces_image_mapping", "SELECT") is False

    def test_the_operation_boundary_still_works(self):
        assert self._table("engine_operation_input", "SELECT") is True
        assert self._table("engine_operation_result_inbox", "INSERT") is True
        assert self._table("engine_operation_result_inbox", "SELECT") is False

    def test_shared_reads_survive_for_the_uncut_families(self):
        # Cyberscript range provision/destroy (#1835 was closed NOT_PLANNED) and
        # the NGFW lookups still read these. Revoking them here would break a
        # live writer; they belong to the residual teardown (#1839).
        assert self._table("mission_control_range", "SELECT") is True
        assert self._table("engine_request", "SELECT") is True
        assert self._table("engine_instance", "SELECT") is True
