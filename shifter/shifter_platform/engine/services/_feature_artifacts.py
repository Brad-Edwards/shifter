"""Single-flight acquisition of backend-owned feature artifacts (ADR-034-R11, R12).

``request_acquisition`` is called wherever an artifact is needed (pack
registration, deploy bootstrap, launch). Under a row lock it either reuses a
ready artifact, joins an in-flight attempt, respects a short failure backoff, or
claims a fresh attempt and starts exactly one acquisition Job after commit.
``run_attempt`` executes inside that Job. Only the attempt holding the row's
current ``attempt_id`` may complete or fail it.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from engine.models import AcquiredFeatureArtifact
from shared.feature_artifacts.npm import FetchedFile, NpmAcquisitionError, clear_workdir, fetch_file
from shared.feature_artifacts.recipes import NpmBinaryRecipe, RecipeError, recipe_for
from shared.raes.content_delivery import normalized_storage_key

logger = logging.getLogger(__name__)

ATTEMPT_LEASE = timedelta(minutes=20)
FAILURE_BACKOFF = timedelta(seconds=60)
_OCTET_STREAM = "application/octet-stream"

State = AcquiredFeatureArtifact.State
StartAttempt = Callable[[UUID, UUID], None]
FetchFile = Callable[..., FetchedFile]


class FeatureArtifactUnavailableError(RuntimeError):
    """A declared feature artifact cannot be made ready for this range."""


@dataclass(frozen=True)
class ArtifactRequest:
    """One feature artifact a scenario declares, before version resolution."""

    source_name: str
    version: str
    platform: str


@dataclass(frozen=True)
class StorageTarget:
    """Where acquired bytes go: the existing content-addressed delivery location."""

    storage: Any
    bucket: str
    prefix: str


def _resolve(request: ArtifactRequest) -> tuple[NpmBinaryRecipe, str]:
    recipe = recipe_for(request.source_name)
    recipe.package_for(request.platform)
    return recipe, recipe.resolve_version(request.version)


def _object_present(row: AcquiredFeatureArtifact, target: StorageTarget) -> bool:
    """A ready row is reusable only while its content-addressed object still matches."""
    try:
        meta = target.storage.head_object(target.bucket, row.storage_key)
    except Exception:
        return False
    return int(meta.get("content_length", -1)) == row.byte_count


def _claim(row: AcquiredFeatureArtifact, now: datetime) -> UUID:
    attempt = uuid4()
    row.state = State.ACQUIRING
    row.attempt_id = attempt
    row.attempt_started_at = now
    row.attempt_expires_at = now + ATTEMPT_LEASE
    row.failure_reason = ""
    row.retry_not_before = None
    row.save()
    return attempt


def request_acquisition(
    request: ArtifactRequest, *, target: StorageTarget, start_attempt: StartAttempt
) -> AcquiredFeatureArtifact:
    """Ensure the artifact is ready or being acquired; never download twice concurrently.

    Raises :class:`RecipeError` when no platform recipe can satisfy the request.
    """
    recipe, version = _resolve(request)
    now = timezone.now()
    with transaction.atomic():
        row, _created = AcquiredFeatureArtifact.objects.select_for_update().get_or_create(
            source_name=request.source_name,
            resolved_version=version,
            platform=request.platform,
            defaults={
                "recipe_id": recipe.recipe_id,
                "payload_kind": recipe.payload_kind,
                "install_policy": recipe.install_policy,
                "state": State.FAILED,
            },
        )
        if row.state == State.READY and _object_present(row, target):
            return row
        in_flight = row.state == State.ACQUIRING and row.attempt_expires_at and row.attempt_expires_at > now
        backing_off = row.state == State.FAILED and row.retry_not_before and row.retry_not_before > now
        if in_flight or backing_off:
            return row
        attempt = _claim(row, now)
        row_id = row.id
        transaction.on_commit(lambda: start_attempt(row_id, attempt))
    logger.info("feature artifact acquisition claimed source=%s version=%s", request.source_name, version)
    return row


def _finish(row_id: UUID, attempt: UUID, **fields: Any) -> bool:
    """Write the attempt outcome only if this attempt still holds the row."""
    updated = AcquiredFeatureArtifact.objects.filter(id=row_id, attempt_id=attempt, state=State.ACQUIRING).update(
        updated_at=timezone.now(), **fields
    )
    return bool(updated)


def run_attempt(
    row_id: UUID, attempt: UUID, *, target: StorageTarget, fetch: FetchFile = fetch_file
) -> AcquiredFeatureArtifact:
    """Acquire the artifact for one claimed attempt (runs in the acquisition Job)."""
    row = AcquiredFeatureArtifact.objects.get(id=row_id)
    if row.attempt_id != attempt or row.state != State.ACQUIRING:
        raise FeatureArtifactUnavailableError("acquisition attempt no longer holds the artifact")
    workdir = Path(tempfile.mkdtemp(prefix="feature-artifact-"))
    try:
        recipe = recipe_for(row.source_name)
        fetched = fetch(recipe.package_for(row.platform), row.resolved_version, recipe.member, workdir)
        key = normalized_storage_key(target.prefix, fetched.sha256)
        if not target.storage.object_exists(target.bucket, key):
            with fetched.path.open("rb") as handle:
                target.storage.upload_file(handle, target.bucket, key, _OCTET_STREAM)
    except (RecipeError, NpmAcquisitionError) as exc:
        _finish(
            row_id,
            attempt,
            state=State.FAILED,
            failure_reason=str(exc)[:256],
            retry_not_before=timezone.now() + FAILURE_BACKOFF,
        )
        raise FeatureArtifactUnavailableError(str(exc)) from None
    except Exception as exc:
        _finish(
            row_id,
            attempt,
            state=State.FAILED,
            failure_reason=f"acquisition failed ({type(exc).__name__})",
            retry_not_before=timezone.now() + FAILURE_BACKOFF,
        )
        raise
    finally:
        clear_workdir(workdir)
    if not _finish(
        row_id,
        attempt,
        state=State.READY,
        storage_key=key,
        sha256=fetched.sha256,
        byte_count=fetched.byte_count,
        upstream_ref=fetched.upstream_ref,
        upstream_integrity=fetched.upstream_integrity,
        acquired_at=timezone.now(),
    ):
        raise FeatureArtifactUnavailableError("acquisition attempt was superseded before completion")
    return AcquiredFeatureArtifact.objects.get(id=row_id)


def await_ready(
    request: ArtifactRequest,
    *,
    target: StorageTarget,
    start_attempt: StartAttempt,
    timeout: timedelta,
    poll: timedelta = timedelta(seconds=5),
    sleep: Callable[[float], None] = time.sleep,
) -> AcquiredFeatureArtifact:
    """Launch gate: return the ready artifact or fail this range (never other ranges).

    Requests acquisition (claiming a fresh attempt if none is usable), then waits
    up to ``timeout`` for it. A failed attempt fails only the caller; a later
    caller starts a new attempt once the backoff elapses.
    """
    if transaction.get_connection().in_atomic_block:
        # The claim commits and the Job starts only after commit; waiting inside
        # an open transaction would wait on an attempt that cannot start.
        raise RuntimeError("await_ready must not run inside a database transaction")
    try:
        _resolve(request)
    except RecipeError as exc:
        raise FeatureArtifactUnavailableError(str(exc)) from None
    deadline = timezone.now() + timeout
    seen_attempt: UUID | None = None
    while True:
        row = request_acquisition(request, target=target, start_attempt=start_attempt)
        if row.state == State.READY:
            return row
        if row.state == State.ACQUIRING:
            seen_attempt = row.attempt_id
        elif seen_attempt is not None or row.retry_not_before is None:
            raise FeatureArtifactUnavailableError(row.failure_reason or "feature artifact acquisition failed")
        if timezone.now() >= deadline:
            raise FeatureArtifactUnavailableError("feature artifact acquisition did not complete in time")
        sleep(poll.total_seconds())
