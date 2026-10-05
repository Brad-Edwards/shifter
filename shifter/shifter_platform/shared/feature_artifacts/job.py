"""Isolated feature-artifact acquisition Job body (ADR-034-R11, R12).

Runs in its own namespace with public HTTPS egress and an identity that may only
write the content-addressed delivery prefix. It has no database access: it
fetches the artifact through a platform recipe, verifies it, stores it under its
content-addressed key, and prints one result line. The provisioner launcher
reads that line, verifies the stored object independently, and finalizes the
inventory row.

Usage: ``python -m shared.feature_artifacts.job <source> <resolved-version> <platform>``
with ``CLOUD_PROVIDER``, ``AWS_REGION``, ``STORAGE_BUCKET_NAME`` and
``RAES_CONTENT_DELIVERY_PREFIX`` in the environment.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from shared.feature_artifacts.npm import FetchedFile, NpmAcquisitionError, clear_workdir, fetch_file
from shared.feature_artifacts.recipes import RecipeError, recipe_for

RESULT_MARKER = "SHIFTER_FEATURE_ARTIFACT_RESULT "
_OCTET_STREAM = "application/octet-stream"

#: Upstream fetch: (package, version, member, workdir) -> verified local file.
Fetch = Callable[[str, str, str, Path], FetchedFile]


class ArtifactStore(Protocol):
    """The one object operation the Job needs."""

    def put(self, path: Path, key: str) -> None: ...


@dataclass(frozen=True)
class AcquisitionResult:
    """What the Job reports; the launcher verifies it before trusting it."""

    ok: bool
    storage_key: str = ""
    sha256: str = ""
    byte_count: int = 0
    upstream_ref: str = ""
    upstream_integrity: str = ""
    reason: str = ""


def content_key(prefix: str, sha256: str) -> str:
    """Content-addressed key, identical to ``shared.raes.content_delivery.normalized_storage_key``."""
    return f"{prefix.strip().strip('/')}/{sha256[:2]}/{sha256}"


class S3ArtifactStore:
    """Minimal S3 writer for the content-addressed prefix (no Django settings)."""

    def __init__(self, bucket: str, region: str, client: Any | None = None) -> None:
        # Local import keeps the module importable without boto3 in tests.
        import boto3

        self._bucket = bucket
        self._client = client or boto3.client("s3", region_name=region)

    def put(self, path: Path, key: str) -> None:
        with path.open("rb") as handle:
            self._client.upload_fileobj(handle, self._bucket, key, ExtraArgs={"ContentType": _OCTET_STREAM})


def _fetch_and_store(
    source_name: str, version: str, platform: str, *, store: ArtifactStore, prefix: str, fetch: Fetch, workdir: Path
) -> AcquisitionResult:
    """Fetch through the source's recipe, store under the content key, and report it."""
    recipe = recipe_for(source_name)
    if recipe.resolve_version(version) != version:
        raise RecipeError("acquisition requires a resolved exact version")
    fetched = fetch(recipe.package_for(platform), version, recipe.member, workdir)
    key = content_key(prefix, fetched.sha256)
    # The key is the content digest, so rewriting an existing object stores the
    # same bytes. Writing unconditionally keeps the Job's grant to PutObject: a
    # HEAD on a missing key without ListBucket answers 403, not 404.
    store.put(fetched.path, key)
    return AcquisitionResult(
        ok=True,
        storage_key=key,
        sha256=fetched.sha256,
        byte_count=fetched.byte_count,
        upstream_ref=fetched.upstream_ref,
        upstream_integrity=fetched.upstream_integrity,
    )


def acquire(
    source_name: str, version: str, platform: str, *, store: ArtifactStore, prefix: str, fetch: Fetch = fetch_file
) -> AcquisitionResult:
    """Fetch, verify and store one artifact; never raises for an acquisition failure."""
    workdir = Path(tempfile.mkdtemp(prefix="feature-artifact-"))
    try:
        return _fetch_and_store(
            source_name, version, platform, store=store, prefix=prefix, fetch=fetch, workdir=workdir
        )
    except (RecipeError, NpmAcquisitionError) as exc:
        return AcquisitionResult(ok=False, reason=str(exc)[:256])
    except Exception as exc:
        return AcquisitionResult(ok=False, reason=f"acquisition failed ({type(exc).__name__})")
    finally:
        clear_workdir(workdir)


def parse_result(log_text: str) -> AcquisitionResult | None:
    """Return the last result line from Job output, or ``None`` if absent or malformed."""
    for line in reversed(log_text.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                data = json.loads(line[len(RESULT_MARKER) :])
                return AcquisitionResult(**{field: data[field] for field in AcquisitionResult.__dataclass_fields__})
            except (ValueError, KeyError, TypeError):
                return None
    return None


def main(argv: list[str]) -> int:
    """Entry point: acquire and write one result line.

    Exits 0 whenever a result line was written, including an acquisition
    failure, because Kubernetes withholds the output of a failed Job and the
    launcher must read the failure reason. Only a crash (no result line) fails
    the Job.
    """
    if len(argv) != 3:
        sys.stderr.write("usage: python -m shared.feature_artifacts.job <source> <resolved-version> <platform>\n")
        return 2
    if os.environ.get("CLOUD_PROVIDER") != "aws":
        result = AcquisitionResult(ok=False, reason="feature-artifact acquisition is not supported on this provider")
    else:
        store = S3ArtifactStore(os.environ["STORAGE_BUCKET_NAME"], os.environ["AWS_REGION"])
        result = acquire(*argv, store=store, prefix=os.environ["RAES_CONTENT_DELIVERY_PREFIX"])
    # The result line on stdout is the Job's output protocol (read from the pod log).
    sys.stdout.write(RESULT_MARKER + json.dumps(asdict(result), sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
