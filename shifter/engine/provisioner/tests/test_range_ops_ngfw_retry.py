"""Tests for NGFW start retry logic in ensure_ngfw_running().

Verifies that transient orchestration failures are retried up to
NGFW_START_MAX_RETRIES times before permanently failing the range.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from range_ops import NGFW_START_MAX_RETRIES, ensure_ngfw_running, pause_ngfw_for_range

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_NGFW_INFO = {
    "ngfw_instance_id": 1,
    "ngfw_request_id": "ngfw-req-uuid",
    "ec2_instance_id": "i-ngfw123",
    "instance_uuid": "ngfw-inst-uuid",
    "status": "paused",
    "app_id": "ngfw-app-uuid",
    "range_id": 42,
}


@pytest.fixture
def _mock_ngfw_deps():
    """Patch all external dependencies used by ensure_ngfw_running.

    Yields a dict of all mocks keyed by short name so individual tests
    can configure them further.
    """
    with (
        patch("range_ops.get_range_ngfw_info") as mock_get_info,
        patch("range_ops._update_ngfw_status") as mock_update,
        patch("range_ops.publish_ngfw_event") as mock_publish,
        patch("range_ops.AWSExecutor") as mock_executor_cls,
        patch("range_ops.OpsOrchestrator") as mock_orch_cls,
        patch("range_ops.NGFWStartPlan") as mock_plan_cls,
        patch("range_ops.time") as mock_time,
    ):
        # Default: return paused NGFW info
        mock_get_info.return_value = dict(SAMPLE_NGFW_INFO)

        # Plan.get_context returns a simple dict
        mock_plan = MagicMock()
        mock_plan.get_context.return_value = {"instance_id": "i-ngfw123"}
        mock_plan_cls.return_value = mock_plan

        # Orchestrator instance
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        yield {
            "get_info": mock_get_info,
            "update_status": mock_update,
            "publish": mock_publish,
            "executor_cls": mock_executor_cls,
            "orch": mock_orch,
            "plan": mock_plan,
            "time": mock_time,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnsureNgfwRunningRetries:
    """Tests for the retry loop inside ensure_ngfw_running."""

    def test_retries_on_failure_then_succeeds(self, _mock_ngfw_deps):
        """Orchestrate is called 3 times when it fails twice then succeeds."""
        mocks = _mock_ngfw_deps
        fail = MagicMock(success=False, error="transient AWS error")
        success = MagicMock(success=True)

        mocks["orch"].orchestrate.side_effect = [fail, fail, success]

        # On retry re-queries, return still-paused so retry continues
        mocks["get_info"].side_effect = [
            dict(SAMPLE_NGFW_INFO),  # initial call
            dict(SAMPLE_NGFW_INFO),  # re-query after attempt 1
            dict(SAMPLE_NGFW_INFO),  # re-query after attempt 2
        ]

        ensure_ngfw_running("req-uuid-123")

        assert mocks["orch"].orchestrate.call_count == NGFW_START_MAX_RETRIES
        assert mocks["time"].sleep.call_count == 2
        # Status should end up as ready, not failed
        mocks["update_status"].assert_any_call(1, "ready")

    def test_fails_after_max_retries(self, _mock_ngfw_deps):
        """RuntimeError is raised after all retry attempts are exhausted."""
        mocks = _mock_ngfw_deps
        fail = MagicMock(success=False, error="persistent AWS error")

        mocks["orch"].orchestrate.side_effect = [fail, fail, fail]

        # Re-query returns still-paused on retry waits
        mocks["get_info"].side_effect = [
            dict(SAMPLE_NGFW_INFO),  # initial call
            dict(SAMPLE_NGFW_INFO),  # re-query after attempt 1
            dict(SAMPLE_NGFW_INFO),  # re-query after attempt 2
        ]

        with pytest.raises(RuntimeError, match="persistent AWS error"):
            ensure_ngfw_running("req-uuid-123")

        assert mocks["orch"].orchestrate.call_count == NGFW_START_MAX_RETRIES
        # time.sleep called for 2 inter-attempt delays (not after the last)
        assert mocks["time"].sleep.call_count == 2
        # Status should be set to failed
        mocks["update_status"].assert_any_call(1, "failed")
        # Failed event published
        mocks["publish"].assert_any_call(
            request_id="ngfw-req-uuid",
            instance_id="ngfw-inst-uuid",
            app_id="ngfw-app-uuid",
            status="failed",
        )

    def test_pausing_waits_for_paused_then_resumes(self, _mock_ngfw_deps):
        """When NGFW is pausing, wait_for_stopped is called before resuming."""
        mocks = _mock_ngfw_deps
        stopping_info = dict(SAMPLE_NGFW_INFO, status="pausing")
        mocks["get_info"].return_value = stopping_info

        # wait_for_stopped succeeds
        mock_executor = MagicMock()
        mock_executor.wait_for_stopped.return_value = MagicMock(success=True)
        mocks["executor_cls"].side_effect = [mock_executor, MagicMock()]

        # Start plan succeeds
        mocks["orch"].orchestrate.return_value = MagicMock(success=True)

        ensure_ngfw_running("req-uuid-123")

        # wait_for_stopped was called with the EC2 instance ID
        mock_executor.wait_for_stopped.assert_called_once_with("i-ngfw123")
        # Status ends up ready
        mocks["update_status"].assert_any_call(1, "ready")

    def test_pausing_wait_fails_raises_error(self, _mock_ngfw_deps):
        """When NGFW is pausing and wait_for_stopped fails, RuntimeError is raised."""
        mocks = _mock_ngfw_deps
        stopping_info = dict(SAMPLE_NGFW_INFO, status="pausing")
        mocks["get_info"].return_value = stopping_info

        # wait_for_stopped fails
        mock_executor = MagicMock()
        mock_executor.wait_for_stopped.return_value = MagicMock(success=False, stderr="timeout waiting for paused")
        mocks["executor_cls"].return_value = mock_executor

        with pytest.raises(RuntimeError, match="NGFW failed to reach paused state"):
            ensure_ngfw_running("req-uuid-123")

        # Start plan should not have been attempted
        mocks["orch"].orchestrate.assert_not_called()


def _executed_sql(call) -> str:
    """Return the whitespace-normalized SQL text of a cursor.execute call.

    Composed (non-string) SQL yields an empty string; the pause flow uses plain
    string statements.
    """
    stmt = call.args[0] if call.args else ""
    return " ".join(stmt.split()) if isinstance(stmt, str) else ""


def test_pause_ngfw_for_range_happy_path(mock_psycopg_connect, monkeypatch, mocker):
    """pause_ngfw_for_range drives the full pause flow against real range_ops code.

    Covers should_pause_ngfw's no-other-ranges branch plus the pausing/paused
    write+publish path, asserting the ResourceStatus-derived values that issue
    #424 wires through the events.py STATUS_* aliases. Only genuine external
    boundaries are faked (ADR-019): the psycopg DB connection and the boto3 AWS
    session. get_range_ngfw_info, should_pause_ngfw, _update_ngfw_status, the
    stop orchestration, and the outbox publish all run for real, so the
    assertion is the observable DB status flow rather than internal mock calls.
    """
    # get_db_connection validates connection env before opening the (mocked)
    # psycopg connection; supply the required non-secret values.
    for key, value in {
        "DB_HOST": "test-db",
        "DB_USER": "shifter_app",
        "DB_NAME": "shifter",
        "CLOUD_REGION": "us-east-2",
    }.items():
        monkeypatch.setenv(key, value)

    _mock_connect, _mock_conn, mock_cursor = mock_psycopg_connect

    # get_range_ngfw_info -> one ready NGFW row (7 columns in query order);
    # should_pause_ngfw -> no other ranges, so the pause proceeds.
    mock_cursor.fetchone.return_value = (
        1,
        "ngfw-req-uuid",
        "i-ngfw123",
        "ngfw-inst-uuid",
        "ready",
        "ngfw-app-uuid",
        42,
    )
    mock_cursor.fetchall.return_value = []

    # AWS boundary: a stubbed boto3 Session whose ec2 stop_instances + waiter
    # return without error, so the real stop orchestration reports success.
    mocker.patch("boto3.Session")

    pause_ngfw_for_range("req-uuid-123")

    # The NGFW instance status is driven pausing -> paused in the database.
    instance_status_writes = [
        call.args[1][0]
        for call in mock_cursor.execute.call_args_list
        if "UPDATE engine_instance" in _executed_sql(call)
    ]
    assert instance_status_writes == ["pausing", "paused"]

    # The same transitions are published to the durable event outbox.
    published = [
        json.loads(call.args[1][2])["status"]
        for call in mock_cursor.execute.call_args_list
        if "engine_range_event_outbox" in _executed_sql(call)
    ]
    assert published == ["pausing", "paused"]
