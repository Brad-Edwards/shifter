"""Generation, canonicalization, and drift checking for the committed
``/api/v1/`` OpenAPI contract (#1329, ADR-040).

The runtime DRF routes, serializers, permissions, and drf-spectacular
annotations remain the authoring source. This module renders that source into
the single committed publication artifact and provides the deterministic
regenerate-and-compare used by the CI drift gate. It is the one place that owns
the artifact path and canonical formatting so the committed file, the SPA type
generation, and the drift check never disagree on bytes.
"""

from __future__ import annotations

import difflib
import io
import json
import shutil
import subprocess  # nosec B404 - fixed argv, no shell; read-only git and the pinned oasdiff binary only
import tempfile
from pathlib import Path
from typing import Any

from django.core.management import call_command

# The published API major. Version-keyed so ``/api/v2/`` can be published beside
# ``/api/v1/`` by selecting another artifact without copying this module.
API_MAJOR = "v1"

# ``shared/api/contract.py`` -> ``shared/api`` -> ``shared`` -> app root.
_APP_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = _APP_ROOT / "openapi"

# Max unified-diff lines surfaced by the drift gate. Keeps CI output bounded and
# never dumps the whole artifact (preflight: bounded diagnostics).
_MAX_DIFF_LINES = 60


def artifact_path(major: str = API_MAJOR) -> Path:
    """Return the committed artifact path for an API major."""
    return ARTIFACT_DIR / f"{major}.json"


def generate_openapi_document() -> str:
    """Return the canonical committed-artifact text for the ``/api/v1/`` surface.

    Generation runs drf-spectacular with validation and fail-on-warn, so an
    unresolved serializer, operation-id collision, schema warning, or invalid
    document raises rather than producing a graceful-fallback artifact.
    """
    buffer = io.StringIO()
    call_command(
        "spectacular",
        format="openapi-json",
        validate=True,
        fail_on_warn=True,
        stdout=buffer,
    )
    document: Any = json.loads(buffer.getvalue())
    return _canonicalize(document)


def _canonicalize(document: Any) -> str:
    """Render an OpenAPI document to stable, review-friendly JSON.

    drf-spectacular emits keys in a deterministic order already; re-dumping with
    a fixed indent, non-escaped unicode, and a trailing newline pins the exact
    bytes the drift gate compares and keeps the end-of-file-fixer hook a no-op.
    """
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_artifact(major: str = API_MAJOR) -> Path:
    """Regenerate and write the committed OpenAPI artifact. Returns its path."""
    path = artifact_path(major)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_openapi_document(), encoding="utf-8")
    return path


def check_drift(major: str = API_MAJOR) -> tuple[bool, str]:
    """Compare a fresh generation against the committed artifact.

    Returns ``(is_current, detail)``. ``detail`` is empty on success and a
    bounded unified diff (or a missing-file message) on drift.
    """
    path = artifact_path(major)
    if not path.exists():
        return False, f"Committed artifact is missing: {path}. Run `manage.py api_contract`."
    current = generate_openapi_document()
    committed = path.read_text(encoding="utf-8")
    if current == committed:
        return True, ""
    diff = difflib.unified_diff(
        committed.splitlines(),
        current.splitlines(),
        fromfile=f"committed:{path.name}",
        tofile="regenerated",
        lineterm="",
    )
    lines = list(diff)
    detail = "\n".join(lines[:_MAX_DIFF_LINES])
    if len(lines) > _MAX_DIFF_LINES:
        detail += f"\n... ({len(lines) - _MAX_DIFF_LINES} more diff lines truncated)"
    return False, detail


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command rooted at the artifact directory."""
    git = shutil.which("git") or "git"
    return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell; read-only git
        [git, *args],
        cwd=ARTIFACT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def resolve_base_document(base_ref: str, major: str = API_MAJOR) -> str | None:
    """Return the committed artifact text from ``base_ref`` (trusted history).

    Reads the artifact from the base branch via ``git show`` so the
    breaking-change comparison uses the already-published contract, never a
    baseline the current PR can rewrite.

    Fails closed: an unresolvable base ref or an unreadable artifact object
    raises, so a broken baseline lookup can never silently let a breaking change
    through. Returns ``None`` ONLY when the base ref resolves but genuinely has no
    committed artifact (the legitimate first-publication case).
    """
    toplevel = _git("rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {toplevel.stderr.strip()}")
    repo_relative = artifact_path(major).relative_to(Path(toplevel.stdout.strip())).as_posix()
    # The base ref itself must resolve; if it does not, fail rather than skip.
    resolved = _git("rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}")
    if resolved.returncode != 0:
        raise RuntimeError(f"base ref {base_ref!r} could not be resolved for the breaking-change gate")
    # A resolvable ref with no artifact object is the only legitimate skip.
    exists = _git("cat-file", "-e", f"{base_ref}:{repo_relative}")
    if exists.returncode != 0:
        return None
    shown = _git("show", f"{base_ref}:{repo_relative}")
    if shown.returncode != 0:
        raise RuntimeError(f"failed to read {repo_relative} at {base_ref}: {shown.stderr.strip()}")
    return shown.stdout


def check_breaking_changes(
    base_text: str,
    current_text: str,
    oasdiff_bin: str = "oasdiff",
) -> tuple[bool, str]:
    """Compare two OpenAPI documents for consumer-breaking changes via oasdiff.

    Returns ``(is_compatible, detail)``. ``oasdiff breaking --fail-on ERR`` exits
    non-zero when it finds a breaking change; the OpenAPI-aware semantics live in
    oasdiff, not in this wrapper. A breaking change to ``/api/v1/`` must instead
    ship as a parallel ``/api/v2/`` with a migration note (ADR-040).
    """
    binary = shutil.which(oasdiff_bin) or oasdiff_bin
    with tempfile.TemporaryDirectory() as tmp:
        base_file = Path(tmp) / "base.json"
        revision_file = Path(tmp) / "revision.json"
        base_file.write_text(base_text, encoding="utf-8")
        revision_file.write_text(current_text, encoding="utf-8")
        result = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell; pinned oasdiff binary
            [binary, "breaking", str(base_file), str(revision_file), "--fail-on", "ERR"],
            capture_output=True,
            text=True,
            check=False,
        )
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0, detail


def check_breaking_against(
    base_ref: str,
    major: str = API_MAJOR,
    oasdiff_bin: str = "oasdiff",
) -> tuple[bool, str]:
    """Run the breaking-change gate for the committed artifact against ``base_ref``.

    Returns ``(ok, detail)``. When the base ref has no committed artifact the gate
    passes (a newly published major has no prior consumer to break).
    """
    base_text = resolve_base_document(base_ref, major)
    if base_text is None:
        return True, f"No committed artifact on {base_ref}; new API major, breaking-change gate skipped."
    path = artifact_path(major)
    if not path.exists():
        return False, f"Committed artifact is missing: {path}. Run `manage.py api_contract`."
    return check_breaking_changes(base_text, path.read_text(encoding="utf-8"), oasdiff_bin)
