"""Tests for enqueue_event_outbox and atomic outbox threading.

Phase 1 (#476): provisioner_db.enqueue_event_outbox inserts a PENDING row into
engine_range_event_outbox. update_range_status and write_provisioned_state
accept an optional outbox_event= so state + event intent commit atomically.

ADR-043 Phase 2 (#1834): provisioner_db.append_operation_result is the shadow,
best-effort append to engine_operation_result_inbox. It mirrors
enqueue_event_outbox's optional-cursor idiom exactly.

Mocks are at the psycopg boundary (get_db_connection); no first-party
internals are patched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock


def _make_conn_mock():
    """Return (conn_mock, cursor_mock) with proper context-manager protocol."""
    cursor_mock = MagicMock()
    conn_mock = MagicMock()
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.__exit__ = MagicMock(return_value=False)
    conn_mock.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
    conn_mock.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn_mock, cursor_mock


class TestEnqueueEventOutbox:
    """Unit tests for provisioner_db.enqueue_event_outbox."""

    def test_own_transaction_path_opens_connection_and_commits(self, monkeypatch):
        """Without a cursor arg, opens its own connection and commits."""
        conn_mock, cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import enqueue_event_outbox

        event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
            "range_id": 42,
            "user_id": 1,
            "new_status": "provisioning",
        }
        enqueue_event_outbox(event)

        cursor_mock.execute.assert_called_once()
        conn_mock.commit.assert_called_once()

    def test_own_transaction_inserts_correct_fields(self, monkeypatch):
        """Verifies event_id, event_type, and JSON payload are in the INSERT."""
        conn_mock, cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import enqueue_event_outbox

        event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
            "range_id": 42,
            "user_id": 1,
            "new_status": "ready",
        }
        enqueue_event_outbox(event)

        execute_args = cursor_mock.execute.call_args
        # Second positional arg is the params tuple
        params = execute_args[0][1]
        assert str(event["event_id"]) in str(params)
        assert event["event_type"] in str(params)
        # payload should be JSON-serialised
        payload_str = params[2] if len(params) > 2 else params[1]
        parsed = json.loads(payload_str)
        assert parsed["range_id"] == 42
        assert parsed["new_status"] == "ready"

    def test_own_transaction_sql_has_on_conflict_do_nothing(self, monkeypatch):
        """INSERT uses ON CONFLICT (event_id) DO NOTHING for idempotency."""
        conn_mock, cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import enqueue_event_outbox

        event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
        }
        enqueue_event_outbox(event)

        sql_arg = cursor_mock.execute.call_args[0][0]
        sql_upper = sql_arg.upper()
        assert "ON CONFLICT" in sql_upper
        assert "DO NOTHING" in sql_upper

    def test_caller_cursor_path_skips_own_connection(self, monkeypatch):
        """When a cursor is provided, no new connection is opened."""
        mock_get_conn = MagicMock()
        monkeypatch.setattr("provisioner_db.get_db_connection", mock_get_conn)

        from provisioner_db import enqueue_event_outbox

        caller_cursor = MagicMock()
        event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
        }
        enqueue_event_outbox(event, cur=caller_cursor)

        mock_get_conn.assert_not_called()
        caller_cursor.execute.assert_called_once()

    def test_caller_cursor_path_does_not_commit(self, monkeypatch):
        """When a cursor is provided, commit is owned by the caller — not called here."""
        mock_get_conn = MagicMock()
        monkeypatch.setattr("provisioner_db.get_db_connection", mock_get_conn)

        # Create a cursor that we can inspect
        caller_cursor = MagicMock()
        # (No conn to check commit on — that's the point: commit is absent)

        from provisioner_db import enqueue_event_outbox

        event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
        }
        enqueue_event_outbox(event, cur=caller_cursor)
        # No connection opened = no commit called anywhere
        mock_get_conn.assert_not_called()

    def test_pending_status_in_insert(self, monkeypatch):
        """The INSERT hardcodes status='PENDING'."""
        conn_mock, cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import enqueue_event_outbox

        event = {"event_id": "aaaaaaaa-0000-0000-0000-000000000001", "event_type": "range.status.updated"}
        enqueue_event_outbox(event)

        sql_arg = cursor_mock.execute.call_args[0][0]
        assert "PENDING" in sql_arg


class TestUpdateRangeStatusWithOutbox:
    """update_range_status threads an optional outbox_event atomically."""

    def test_enqueues_event_in_same_cursor_block(self, monkeypatch):
        """outbox_event= causes enqueue_event_outbox to be called with the shared cursor."""
        conn_mock, _cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        enqueue_calls = []

        def _capture_enqueue(event, *, cur=None):
            enqueue_calls.append({"event": event, "cur": cur})

        monkeypatch.setattr("provisioner_db.enqueue_event_outbox", _capture_enqueue)

        from provisioner_db import update_range_status

        outbox_event = {
            "event_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "event_type": "range.status.updated",
            "new_status": "paused",
        }
        update_range_status(42, "paused", outbox_event=outbox_event)

        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["event"] is outbox_event
        # cur= must be set (not None) — same cursor as the UPDATE
        assert enqueue_calls[0]["cur"] is not None

    def test_no_enqueue_when_outbox_event_is_none(self, monkeypatch):
        """When outbox_event is None, enqueue_event_outbox is never called."""
        conn_mock, _cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        mock_enqueue = MagicMock()
        monkeypatch.setattr("provisioner_db.enqueue_event_outbox", mock_enqueue)

        from provisioner_db import update_range_status

        update_range_status(42, "paused")

        mock_enqueue.assert_not_called()

    def test_both_update_and_enqueue_before_commit(self, monkeypatch):
        """DB update and outbox INSERT are both executed before conn.commit()."""
        conn_mock, cursor_mock = _make_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        call_log: list[str] = []
        original_execute = cursor_mock.execute

        def _track_execute(*args, **kwargs):
            call_log.append("execute")
            return original_execute(*args, **kwargs)

        cursor_mock.execute = _track_execute
        conn_mock.commit = MagicMock(side_effect=lambda: call_log.append("commit"))

        real_enqueue_calls: list[dict] = []

        def _real_enqueue(event, *, cur=None):
            real_enqueue_calls.append({"event": event, "cur": cur})
            # simulate enqueue using the cursor
            call_log.append("enqueue_execute")

        monkeypatch.setattr("provisioner_db.enqueue_event_outbox", _real_enqueue)

        from provisioner_db import update_range_status

        outbox_event = {"event_id": "aaa", "event_type": "range.status.updated"}
        update_range_status(42, "paused", outbox_event=outbox_event)

        # All executes happen before commit
        assert "execute" in call_log
        assert "enqueue_execute" in call_log
        assert "commit" in call_log
        execute_idx = max(i for i, v in enumerate(call_log) if v in ("execute", "enqueue_execute"))
        commit_idx = call_log.index("commit")
        assert execute_idx < commit_idx


class TestWriteProvisionedStateWithOutbox:
    """write_provisioned_state threads an optional outbox_event atomically."""

    def _make_full_conn_mock(self):
        """Connection mock where rowcount > 0 so write_provisioned_state succeeds."""
        cursor_mock = MagicMock()
        cursor_mock.rowcount = 1

        conn_mock = MagicMock()
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        conn_mock.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn_mock, cursor_mock

    def test_enqueues_event_in_same_cursor_block(self, monkeypatch):
        """outbox_event= causes enqueue_event_outbox to be called with the shared cursor."""
        conn_mock, _cursor_mock = self._make_full_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        # Minimal stubs for state helpers
        monkeypatch.setattr("provisioner_db._get_cloud_provider", MagicMock(return_value="aws"))
        monkeypatch.setattr(
            "provisioner_db._build_subnet_state",
            MagicMock(return_value={"cloud_provider": "aws"}),
        )
        monkeypatch.setattr(
            "provisioner_db._build_instance_state",
            MagicMock(return_value={"cloud_provider": "aws"}),
        )
        monkeypatch.setattr(
            "provisioner_db._build_provisioned_instance_payload",
            MagicMock(return_value={}),
        )

        enqueue_calls: list[dict] = []

        def _capture(event, *, cur=None):
            enqueue_calls.append({"event": event, "cur": cur})

        monkeypatch.setattr("provisioner_db.enqueue_event_outbox", _capture)

        from provisioner_db import write_provisioned_state

        outbox_event = {"event_id": "aaa", "event_type": "range.status.updated"}
        write_provisioned_state(
            range_id=42,
            subnets={"attack": {"uuid": "sub-uuid-1"}},
            instances=[{"uuid": "inst-uuid-1"}],
            outbox_event=outbox_event,
        )

        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["event"] is outbox_event
        assert enqueue_calls[0]["cur"] is not None

    def test_no_enqueue_when_outbox_event_is_none(self, monkeypatch):
        """When outbox_event is None, enqueue_event_outbox is never called."""
        conn_mock, _cursor_mock = self._make_full_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))
        monkeypatch.setattr("provisioner_db._get_cloud_provider", MagicMock(return_value="aws"))
        monkeypatch.setattr(
            "provisioner_db._build_subnet_state",
            MagicMock(return_value={"cloud_provider": "aws"}),
        )
        monkeypatch.setattr(
            "provisioner_db._build_instance_state",
            MagicMock(return_value={"cloud_provider": "aws"}),
        )
        monkeypatch.setattr(
            "provisioner_db._build_provisioned_instance_payload",
            MagicMock(return_value={}),
        )

        mock_enqueue = MagicMock()
        monkeypatch.setattr("provisioner_db.enqueue_event_outbox", mock_enqueue)

        from provisioner_db import write_provisioned_state

        write_provisioned_state(
            range_id=42,
            subnets={"attack": {"uuid": "sub-uuid-1"}},
            instances=[{"uuid": "inst-uuid-1"}],
        )

        mock_enqueue.assert_not_called()


class TestAppendOperationResult:
    """Unit tests for provisioner_db.append_operation_result (ADR-043 Phase 2, #1834)."""

    OPERATION_ID = "11111111-1111-1111-1111-111111111111"
    REQUEST_ID = "22222222-2222-2222-2222-222222222222"

    def test_own_transaction_path_opens_connection_and_commits(self, monkeypatch):
        """Without a cursor arg, opens its own connection and commits."""
        conn_mock, cursor_mock = _make_conn_mock()
        cursor_mock.rowcount = 1
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import append_operation_result

        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "ready"},
        )

        cursor_mock.execute.assert_called_once()
        conn_mock.commit.assert_called_once()

    def test_insert_sets_every_non_defaulted_column_including_pending_disposition(self, monkeypatch):
        """The raw INSERT sets every column the model defaults ORM-side, not DB-side."""
        conn_mock, cursor_mock = _make_conn_mock()
        cursor_mock.rowcount = 1
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from shared.operation_envelope import build_operation_envelope, canonical_payload_digest

        from provisioner_db import append_operation_result

        payload = {"status": "ready", "range_id": 42}
        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload=payload,
        )

        sql_arg, params = cursor_mock.execute.call_args[0]
        sql_upper = sql_arg.upper()
        for column in (
            "OPERATION_ID",
            "REQUEST_ID",
            "RESOURCE",
            "OPERATION",
            "CONTRACT_VERSION",
            "RESULT_KIND",
            "RESULT_IDENTITY",
            "PAYLOAD_DIGEST",
            "ENVELOPE",
            "DISPOSITION",
            "DISPOSITION_DETAIL",
            "CREATED_AT",
        ):
            assert column in sql_upper
        assert "ON CONFLICT" in sql_upper
        assert "DO NOTHING" in sql_upper
        assert "'PENDING'" in sql_arg
        assert "NOW()" in sql_upper

        expected_envelope = build_operation_envelope(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            payload=payload,
        )
        expected_digest = canonical_payload_digest(payload)
        assert params == (
            self.OPERATION_ID,
            self.REQUEST_ID,
            "range",
            "provision",
            "1",
            "TERMINAL_SUCCESS",
            f"{self.OPERATION_ID}:TERMINAL_SUCCESS",
            expected_digest,
            json.dumps(expected_envelope),
        )

    def test_result_identity_is_deterministic_per_operation_and_kind(self, monkeypatch):
        """result_identity is f'{operation_id}:{result_kind}', not payload-derived."""
        conn_mock, cursor_mock = _make_conn_mock()
        cursor_mock.rowcount = 1
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        from provisioner_db import append_operation_result

        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="ngfw",
            operation="start",
            result_kind="RESOURCE_STATE",
            result_payload={"status": "ready"},
        )

        params = cursor_mock.execute.call_args[0][1]
        assert params[6] == f"{self.OPERATION_ID}:RESOURCE_STATE"

    def test_caller_cursor_path_skips_own_connection_and_uses_savepoint(self, monkeypatch):
        """When cur is provided, no new connection opens and the append rides a SAVEPOINT."""
        mock_get_conn = MagicMock()
        monkeypatch.setattr("provisioner_db.get_db_connection", mock_get_conn)

        from provisioner_db import append_operation_result

        caller_cursor = MagicMock()
        caller_cursor.rowcount = 1

        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="destroy",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "destroyed"},
            cur=caller_cursor,
        )

        mock_get_conn.assert_not_called()
        caller_cursor.execute.assert_called_once()
        # SAVEPOINT semantics: the shared cursor's connection opens a nested
        # transaction so a shadow failure can only unwind this append.
        caller_cursor.connection.transaction.assert_called_once()

    def test_caller_cursor_path_does_not_commit(self, monkeypatch):
        """When cur is provided, commit is owned by the caller."""
        mock_get_conn = MagicMock()
        monkeypatch.setattr("provisioner_db.get_db_connection", mock_get_conn)

        from provisioner_db import append_operation_result

        caller_cursor = MagicMock()
        caller_cursor.rowcount = 1

        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="destroy",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "destroyed"},
            cur=caller_cursor,
        )

        mock_get_conn.assert_not_called()

    def test_replay_with_identical_digest_is_a_harmless_no_op(self, monkeypatch):
        """Same result_identity + same payload digest: no warning, no raise."""
        from shared.operation_envelope import canonical_payload_digest

        from provisioner_db import append_operation_result

        payload = {"status": "ready"}
        digest = canonical_payload_digest(payload)
        caller_cursor = MagicMock()
        caller_cursor.rowcount = 0  # ON CONFLICT DO NOTHING: zero rows inserted
        caller_cursor.fetchone.return_value = (digest,)
        mock_warning = MagicMock()
        monkeypatch.setattr("provisioner_db.logger.warning", mock_warning)

        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload=payload,
            cur=caller_cursor,
        )

        mock_warning.assert_not_called()
        assert caller_cursor.execute.call_count == 2  # INSERT + SELECT digest

    def test_conflicting_digest_logs_a_fixed_reason_code_and_does_not_raise(self, monkeypatch):
        """Same result_identity, different payload digest: logged WARNING, never raised."""
        from provisioner_db import append_operation_result

        caller_cursor = MagicMock()
        caller_cursor.rowcount = 0  # ON CONFLICT DO NOTHING: zero rows inserted
        caller_cursor.fetchone.return_value = ("sha256:" + "0" * 64,)
        mock_warning = MagicMock()
        monkeypatch.setattr("provisioner_db.logger.warning", mock_warning)

        # Should not raise -- shadow appends are best-effort; direct SQL stays authoritative.
        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "ready", "different": True},
            cur=caller_cursor,
        )

        mock_warning.assert_called_once()
        assert mock_warning.call_args[0][1] == "operation_result_inbox_conflict"

    def test_unexpected_error_is_logged_and_swallowed_not_raised(self, monkeypatch):
        """A shadow-append failure must never fail an authoritative provisioning operation."""
        from provisioner_db import append_operation_result

        caller_cursor = MagicMock()
        caller_cursor.execute.side_effect = RuntimeError("boom")
        mock_warning = MagicMock()
        monkeypatch.setattr("provisioner_db.logger.warning", mock_warning)

        # Must not raise.
        append_operation_result(
            operation_id=self.OPERATION_ID,
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "ready"},
            cur=caller_cursor,
        )

        mock_warning.assert_called_once()
        assert mock_warning.call_args[0][1] == "operation_result_inbox_append_failed"

    def test_invalid_envelope_input_is_swallowed_not_raised(self, monkeypatch):
        """An invalid operation_id/resource/operation fails envelope validation but never raises."""
        from provisioner_db import append_operation_result

        mock_get_conn = MagicMock()
        monkeypatch.setattr("provisioner_db.get_db_connection", mock_get_conn)
        mock_warning = MagicMock()
        monkeypatch.setattr("provisioner_db.logger.warning", mock_warning)

        # Not a valid UUID -- build_operation_envelope raises OperationEnvelopeError.
        append_operation_result(
            operation_id="not-a-uuid",
            request_id=self.REQUEST_ID,
            resource="range",
            operation="provision",
            result_kind="TERMINAL_SUCCESS",
            result_payload={"status": "ready"},
        )

        mock_warning.assert_called_once()
        mock_get_conn.assert_not_called()


class TestAppendOperationResultWiredIntoWriteProvisionedState:
    """write_provisioned_state issues the shadow append only when operation_id is present."""

    def _make_full_conn_mock(self):
        cursor_mock = MagicMock()
        cursor_mock.rowcount = 1
        conn_mock = MagicMock()
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        conn_mock.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn_mock, cursor_mock

    def _patch_state_helpers(self, monkeypatch):
        monkeypatch.setattr("provisioner_db._get_cloud_provider", MagicMock(return_value="aws"))
        monkeypatch.setattr("provisioner_db._build_subnet_state", MagicMock(return_value={"cloud_provider": "aws"}))
        monkeypatch.setattr("provisioner_db._build_instance_state", MagicMock(return_value={"cloud_provider": "aws"}))
        monkeypatch.setattr("provisioner_db._build_provisioned_instance_payload", MagicMock(return_value={}))

    def test_appends_when_operation_id_present(self, monkeypatch):
        conn_mock, _cursor_mock = self._make_full_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))
        self._patch_state_helpers(monkeypatch)

        append_calls: list[dict] = []

        def _capture(*, cur=None, **kwargs):
            append_calls.append({"cur": cur, **kwargs})

        monkeypatch.setattr("provisioner_db.append_operation_result", _capture)

        from provisioner_db import write_provisioned_state

        write_provisioned_state(
            range_id=42,
            subnets={"attack": {"uuid": "sub-uuid-1"}},
            instances=[{"uuid": "inst-uuid-1"}],
            request_id="req-1",
            operation_id="op-1",
        )

        assert len(append_calls) == 1
        assert append_calls[0]["operation_id"] == "op-1"
        assert append_calls[0]["request_id"] == "req-1"
        assert append_calls[0]["resource"] == "range"
        assert append_calls[0]["operation"] == "provision"
        assert append_calls[0]["result_kind"] == "TERMINAL_SUCCESS"
        assert append_calls[0]["cur"] is not None

    def test_skips_append_when_operation_id_is_none(self, monkeypatch):
        conn_mock, _cursor_mock = self._make_full_conn_mock()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))
        self._patch_state_helpers(monkeypatch)

        mock_append = MagicMock()
        monkeypatch.setattr("provisioner_db.append_operation_result", mock_append)

        from provisioner_db import write_provisioned_state

        write_provisioned_state(
            range_id=42,
            subnets={"attack": {"uuid": "sub-uuid-1"}},
            instances=[{"uuid": "inst-uuid-1"}],
        )

        mock_append.assert_not_called()


class TestAppendOperationResultWiredIntoMarkRangeInstancesDestroyed:
    """mark_range_instances_destroyed issues the shadow append only when operation_id is present."""

    def _make_conn(self):
        cursor_mock = MagicMock()
        cursor_mock.rowcount = 1
        conn_mock = MagicMock()
        conn_mock.__enter__ = MagicMock(return_value=conn_mock)
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        conn_mock.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn_mock, cursor_mock

    def test_appends_when_operation_id_present(self, monkeypatch):
        conn_mock, _cursor_mock = self._make_conn()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        mock_append = MagicMock()
        monkeypatch.setattr("provisioner_db.append_operation_result", mock_append)

        from provisioner_db import mark_range_instances_destroyed

        mark_range_instances_destroyed(42, request_id="req-1", operation_id="op-1")

        mock_append.assert_called_once()
        _args, kwargs = mock_append.call_args
        assert kwargs["operation_id"] == "op-1"
        assert kwargs["request_id"] == "req-1"
        assert kwargs["resource"] == "range"
        assert kwargs["operation"] == "destroy"
        assert kwargs["result_kind"] == "TERMINAL_SUCCESS"
        assert kwargs["cur"] is not None

    def test_skips_append_when_operation_id_is_none(self, monkeypatch):
        conn_mock, _cursor_mock = self._make_conn()
        monkeypatch.setattr("provisioner_db.get_db_connection", MagicMock(return_value=conn_mock))

        mock_append = MagicMock()
        monkeypatch.setattr("provisioner_db.append_operation_result", mock_append)

        from provisioner_db import mark_range_instances_destroyed

        mark_range_instances_destroyed(42)

        mock_append.assert_not_called()
