"""Feature artifacts are acquired once, reused, fenced per attempt, and fail per range."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from django.db import transaction
from django.utils import timezone

from engine.models import AcquiredFeatureArtifact
from engine.services._feature_artifacts import (
    ArtifactRequest,
    FeatureArtifactUnavailableError,
    StorageTarget,
    await_ready,
    request_acquisition,
    run_attempt,
)
from shared.feature_artifacts.npm import FetchedFile, NpmAcquisitionError
from shared.feature_artifacts.recipes import OPEN_VERSION, RecipeError

State = AcquiredFeatureArtifact.State
BINARY = b"claude-binary" * 1000
SHA = hashlib.sha256(BINARY).hexdigest()
REQUEST = ArtifactRequest(source_name="claude-code", version=OPEN_VERSION, platform="linux-x64-glibc")


class FakeObjectStorage:
    """In-memory object storage at the cloud boundary."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads = 0

    def object_exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def head_object(self, bucket: str, key: str) -> dict:
        return {"content_length": len(self.objects[(bucket, key)]), "etag": "x"}

    def upload_file(self, file_obj, bucket: str, key: str, content_type: str = "") -> None:
        self.uploads += 1
        self.objects[(bucket, key)] = file_obj.read()


@pytest.fixture
def harness():
    """Storage target, a recording Job starter, and a controllable upstream fetch."""

    class Harness:
        def __init__(self) -> None:
            self.storage = FakeObjectStorage()
            self.target = StorageTarget(storage=self.storage, bucket="assets", prefix="raes/content-delivery")
            self.started: list[tuple[UUID, UUID]] = []
            self.fetches = 0
            self.fail_with: Exception | None = None

        def start(self, row_id: UUID, attempt: UUID) -> None:
            self.started.append((row_id, attempt))

        def fetch(self, package: str, version: str, member: str, workdir: Path) -> FetchedFile:
            self.fetches += 1
            if self.fail_with is not None:
                raise self.fail_with
            path = workdir / "artifact"
            path.write_bytes(BINARY)
            return FetchedFile(path, SHA, len(BINARY), f"npm:{package}@{version}", "sha512-x")

        def run_started(self) -> AcquiredFeatureArtifact:
            row_id, attempt = self.started[-1]
            return run_attempt(row_id, attempt, target=self.target, fetch=self.fetch)

    return Harness()


@pytest.mark.django_db
def test_concurrent_requests_share_one_attempt_and_ready_artifacts_are_reused(
    harness, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        first = request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
        second = request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)

    assert first.state == second.state == State.ACQUIRING
    assert first.resolved_version == "2.1.289"
    assert len(harness.started) == 1

    ready = harness.run_started()
    assert ready.state == State.READY
    assert (ready.sha256, ready.byte_count) == (SHA, len(BINARY))
    assert ready.storage_key == f"raes/content-delivery/{SHA[:2]}/{SHA}"
    assert harness.storage.objects[("assets", ready.storage_key)] == BINARY

    with django_capture_on_commit_callbacks(execute=True):
        reused = request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    assert reused.state == State.READY
    assert (len(harness.started), harness.fetches, harness.storage.uploads) == (1, 1, 1)


@pytest.mark.django_db
def test_vanished_object_triggers_reacquisition(harness, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    ready = harness.run_started()
    harness.storage.objects.clear()

    with django_capture_on_commit_callbacks(execute=True):
        again = request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)

    assert again.state == State.ACQUIRING
    assert len(harness.started) == 2
    assert harness.run_started().storage_key == ready.storage_key


@pytest.mark.django_db
def test_failure_backs_off_then_a_later_request_retries(harness, django_capture_on_commit_callbacks):
    harness.fail_with = NpmAcquisitionError("npm package version not found")
    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    with pytest.raises(FeatureArtifactUnavailableError, match="not found"):
        harness.run_started()

    failed = AcquiredFeatureArtifact.objects.get()
    assert failed.state == State.FAILED
    assert failed.failure_reason == "npm package version not found"

    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    assert len(harness.started) == 1  # within backoff: no new download

    AcquiredFeatureArtifact.objects.update(retry_not_before=timezone.now() - timedelta(seconds=1))
    harness.fail_with = None
    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    assert len(harness.started) == 2
    assert harness.run_started().state == State.READY


@pytest.mark.django_db
def test_superseded_attempts_cannot_write_the_row(harness, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)
    row_id, stale = harness.started[-1]
    AcquiredFeatureArtifact.objects.update(attempt_expires_at=timezone.now() - timedelta(seconds=1))
    with django_capture_on_commit_callbacks(execute=True):
        request_acquisition(REQUEST, target=harness.target, start_attempt=harness.start)

    with pytest.raises(FeatureArtifactUnavailableError, match="no longer holds"):
        run_attempt(row_id, stale, target=harness.target, fetch=harness.fetch)
    assert harness.run_started().state == State.READY
    assert harness.fetches == 1


@pytest.mark.django_db
def test_unknown_sources_fail_closed_without_creating_rows(harness):
    with pytest.raises(RecipeError):
        request_acquisition(
            ArtifactRequest("arbitrary-url-fetch", OPEN_VERSION, "linux-x64-glibc"),
            target=harness.target,
            start_attempt=harness.start,
        )
    assert not AcquiredFeatureArtifact.objects.exists()


@pytest.mark.django_db(transaction=True)
class TestLaunchGate:
    def test_waits_for_the_shared_attempt_and_returns_the_ready_artifact(self, harness):
        def start_and_run(row_id: UUID, attempt: UUID) -> None:
            harness.start(row_id, attempt)
            harness.run_started()

        row = await_ready(REQUEST, target=harness.target, start_attempt=start_and_run, timeout=timedelta(seconds=5))
        assert row.state == State.READY

    def test_failed_attempt_fails_only_the_waiting_range(self, harness):
        harness.fail_with = NpmAcquisitionError("npm tarball integrity mismatch")

        def start_and_run(row_id: UUID, attempt: UUID) -> None:
            harness.start(row_id, attempt)
            with pytest.raises(FeatureArtifactUnavailableError):
                harness.run_started()

        with pytest.raises(FeatureArtifactUnavailableError, match="integrity mismatch"):
            await_ready(REQUEST, target=harness.target, start_attempt=start_and_run, timeout=timedelta(seconds=5))
        assert AcquiredFeatureArtifact.objects.get().state == State.FAILED

    def test_unknown_recipe_and_timeout_fail_the_range(self, harness):
        with pytest.raises(FeatureArtifactUnavailableError, match="no platform acquisition recipe"):
            await_ready(
                ArtifactRequest("nope", OPEN_VERSION, "linux-x64-glibc"),
                target=harness.target,
                start_attempt=harness.start,
                timeout=timedelta(seconds=5),
            )
        with pytest.raises(FeatureArtifactUnavailableError, match="did not complete in time"):
            await_ready(
                REQUEST,
                target=harness.target,
                start_attempt=harness.start,
                timeout=timedelta(0),
                sleep=lambda _seconds: None,
            )

    def test_refuses_to_wait_inside_a_transaction(self, harness):
        with transaction.atomic(), pytest.raises(RuntimeError, match="inside a database transaction"):
            await_ready(REQUEST, target=harness.target, start_attempt=harness.start, timeout=timedelta(seconds=1))
        assert not harness.started
