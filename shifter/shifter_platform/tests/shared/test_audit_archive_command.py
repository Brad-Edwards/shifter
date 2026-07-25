"""Tests for the rehomed ``audit_archive`` management command (#1374).

The issue's acceptance criterion is that ``audit_archive`` still works from its
new home in ``shared``. It moved with no test at all, so these cover the command
end to end through ``call_command``: only the S3 boundary is faked (per the
boundary-mock policy), while batching, the gzipped JSON Lines payload, the
retention cutoff, deletion, and every early-return guard run for real.
"""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from botocore.exceptions import ClientError
from django.core.management import call_command
from django.utils import timezone

from shared.models import AuditLog

pytestmark = pytest.mark.django_db


class FakeS3Client:
    """Records ``put_object`` calls; optionally fails to exercise the error path."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject")
        return {}


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    """Bind a fake S3 client and a stable account id (skips the STS fallback)."""
    client = FakeS3Client()
    monkeypatch.setattr("boto3.client", lambda service, *a, **kw: client)
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    return client


@pytest.fixture
def bucket(settings) -> str:
    settings.LOGS_BUCKET_NAME = "shifter-logs"
    return "shifter-logs"


def _make_rows(count: int, *, age_days: int) -> list[int]:
    """Create ``count`` audit rows aged ``age_days`` days. Returns their ids.

    ``timestamp`` is ``auto_now_add``, so it is backdated with an UPDATE after
    creation rather than passed to ``create``.
    """
    ids = []
    for index in range(count):
        row = AuditLog.objects.create(
            entity_type="range",
            entity_id=index,
            action="create",
            actor_type="system",
            actor_id=index,
            context=f"row-{index}",
            request_id=f"req-{index}",
        )
        ids.append(row.id)
    AuditLog.objects.filter(id__in=ids).update(timestamp=timezone.now() - timedelta(days=age_days))
    return ids


def _run(**options: Any) -> str:
    out = StringIO()
    call_command("audit_archive", stdout=out, **options)
    return out.getvalue()


class TestNothingToArchive:
    def test_reports_and_exits_when_no_rows_are_old_enough(self, s3: FakeS3Client, bucket: str) -> None:
        _make_rows(3, age_days=1)
        output = _run(retention_days=90)
        assert "No audit logs to archive" in output
        assert s3.calls == []
        assert AuditLog.objects.count() == 3


class TestDryRun:
    def test_lists_samples_and_changes_nothing(self, s3: FakeS3Client, bucket: str) -> None:
        _make_rows(2, age_days=120)
        output = _run(dry_run=True, retention_days=90)
        assert "Dry run - no changes made" in output
        assert s3.calls == [], "dry run must not upload"
        assert AuditLog.objects.count() == 2, "dry run must not delete"


class TestMissingBucket:
    def test_fails_loudly_without_uploading(self, s3: FakeS3Client, settings, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no destination resolvable from any of its four sources, the
        command must report and stop without deleting anything."""
        settings.LOGS_BUCKET_NAME = None
        settings.AUDIT_ARCHIVE_BUCKET = None
        monkeypatch.delenv("LOGS_BUCKET_NAME", raising=False)
        monkeypatch.delenv("AUDIT_ARCHIVE_BUCKET", raising=False)
        _make_rows(1, age_days=120)

        output = _run(retention_days=90)

        assert "LOGS_BUCKET_NAME not configured" in output
        assert s3.calls == []
        assert AuditLog.objects.count() == 1, "nothing may be deleted when the destination is unknown"

    @pytest.mark.parametrize(
        "source",
        [
            "settings.LOGS_BUCKET_NAME",
            "env.LOGS_BUCKET_NAME",
            "settings.AUDIT_ARCHIVE_BUCKET",
            "env.AUDIT_ARCHIVE_BUCKET",
        ],
    )
    def test_each_bucket_source_is_honored(
        self, s3: FakeS3Client, settings, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        """All four documented destination sources resolve, so the fallback chain
        cannot silently lose one."""
        settings.LOGS_BUCKET_NAME = None
        settings.AUDIT_ARCHIVE_BUCKET = None
        monkeypatch.delenv("LOGS_BUCKET_NAME", raising=False)
        monkeypatch.delenv("AUDIT_ARCHIVE_BUCKET", raising=False)
        kind, name = source.split(".")
        if kind == "settings":
            setattr(settings, name, "from-settings")
            expected = "from-settings"
        else:
            monkeypatch.setenv(name, "from-env")
            expected = "from-env"
        _make_rows(1, age_days=120)

        _run(retention_days=90)

        assert len(s3.calls) == 1
        assert s3.calls[0]["Bucket"] == expected


class TestArchiveRoundTrip:
    def test_uploads_gzipped_jsonl_and_deletes_the_rows(self, s3: FakeS3Client, bucket: str) -> None:
        ids = _make_rows(3, age_days=120)
        recent = _make_rows(1, age_days=1)

        output = _run(retention_days=90)

        assert len(s3.calls) == 1
        call = s3.calls[0]
        assert call["Bucket"] == bucket
        assert call["Key"].startswith("audit-archive/")
        assert call["Key"].endswith(".jsonl.gz")
        assert call["ContentEncoding"] == "gzip"
        assert call["ContentType"] == "application/x-ndjson"
        assert call["ExpectedBucketOwner"] == "123456789012"

        # The payload is real gzipped JSON Lines carrying the evidentiary fields.
        lines = gzip.decompress(call["Body"]).decode("utf-8").splitlines()
        assert len(lines) == 3
        records = [json.loads(line) for line in lines]
        assert {r["id"] for r in records} == set(ids)
        assert all(r["context"].startswith("row-") for r in records)
        assert all(r["request_id"].startswith("req-") for r in records)
        assert all(r["timestamp"] for r in records)

        # Archived rows are gone; the row inside the retention window survives.
        assert list(AuditLog.objects.values_list("id", flat=True)) == recent
        assert "Records archived: 3" in output
        assert "Records deleted: 3" in output

    def test_no_delete_archives_but_retains_rows(self, s3: FakeS3Client, bucket: str) -> None:
        ids = _make_rows(2, age_days=120)
        output = _run(retention_days=90, no_delete=True)
        assert len(s3.calls) == 1
        assert set(AuditLog.objects.values_list("id", flat=True)) == set(ids)
        assert "Records archived: 2" in output
        assert "Records deleted: 0" in output

    def test_no_delete_over_multiple_batches_terminates(self, s3: FakeS3Client, bucket: str) -> None:
        """Regression: ``--no-delete`` used to loop forever.

        Nothing is deleted, so the batch window has to advance. Before the fix
        the same first batch was re-read and re-uploaded to S3 indefinitely; this
        test hangs rather than fails if that returns, which is why the upload
        count is asserted exactly.
        """
        ids = _make_rows(5, age_days=120)
        output = _run(retention_days=90, no_delete=True, batch_size=2)
        assert len(s3.calls) == 3, "5 rows at batch_size=2 is exactly three uploads, not an endless re-read"
        archived = [
            json.loads(line) for call in s3.calls for line in gzip.decompress(call["Body"]).decode().splitlines()
        ]
        assert {r["id"] for r in archived} == set(ids), "every row archived exactly once"
        assert len(archived) == 5
        assert set(AuditLog.objects.values_list("id", flat=True)) == set(ids)
        assert "Records archived: 5" in output
        assert "Records deleted: 0" in output

    def test_batches_are_uploaded_separately(self, s3: FakeS3Client, bucket: str) -> None:
        _make_rows(5, age_days=120)
        output = _run(retention_days=90, batch_size=2)
        assert len(s3.calls) == 3, "5 rows at batch_size=2 is three uploads"
        assert sum(len(gzip.decompress(c["Body"]).decode().splitlines()) for c in s3.calls) == 5
        assert AuditLog.objects.count() == 0
        assert "Records archived: 5" in output


class TestUploadFailure:
    def test_s3_error_stops_the_run_and_keeps_the_rows(self, monkeypatch: pytest.MonkeyPatch, bucket: str) -> None:
        failing = FakeS3Client(fail=True)
        monkeypatch.setattr("boto3.client", lambda service, *a, **kw: failing)
        monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
        ids = _make_rows(4, age_days=120)

        output = _run(retention_days=90, batch_size=2)

        assert "S3 upload failed" in output
        assert len(failing.calls) == 1, "the run stops after the first failed upload"
        assert set(AuditLog.objects.values_list("id", flat=True)) == set(ids), (
            "a failed upload must never delete audit rows"
        )
        assert "Records archived: 0" in output


class TestExpectedBucketOwner:
    def test_sts_failure_disables_the_check_without_failing_the_archive(
        self, monkeypatch: pytest.MonkeyPatch, bucket: str
    ) -> None:
        client = FakeS3Client()

        def fake_boto_client(service: str, *args: Any, **kwargs: Any) -> Any:
            if service == "sts":
                raise RuntimeError("no credentials")
            return client

        monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
        monkeypatch.setattr("boto3.client", fake_boto_client)
        _make_rows(1, age_days=120)

        output = _run(retention_days=90)

        assert "ExpectedBucketOwner check disabled" in output
        assert len(client.calls) == 1
        assert "ExpectedBucketOwner" not in client.calls[0]
        assert AuditLog.objects.count() == 0, "the archive still completes"
