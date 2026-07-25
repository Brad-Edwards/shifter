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
import subprocess  # nosec B404 - fixed argv, no shell; read-only git and pinned oasdiff only  # NOSONAR
import tempfile
from datetime import date
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

# Fixed name of the pinned OpenAPI-aware compatibility checker, resolved from
# PATH. Not caller-configurable: the binary is provisioned by CI (ADR-037 pin +
# checksum) so no external/untrusted value ever reaches the subprocess argv.
_OASDIFF_BIN = "oasdiff"


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
    document: dict[str, Any] = json.loads(buffer.getvalue())
    return _canonicalize(document)


def _canonicalize(document: dict[str, Any]) -> str:
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


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command (defaults to the artifact directory)."""
    git = shutil.which("git") or "git"
    return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell; read-only git
        [git, *args],
        cwd=cwd or ARTIFACT_DIR,
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
    toplevel_result = _git("rev-parse", "--show-toplevel")
    if toplevel_result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {toplevel_result.stderr.strip()}")
    toplevel = Path(toplevel_result.stdout.strip())
    # Path-scoped git commands run from the repo root so the repo-relative
    # pathspec (ls-tree) and the ``<ref>:<path>`` lookup (git show) resolve
    # consistently regardless of where the process was launched.
    repo_relative = artifact_path(major).relative_to(toplevel).as_posix()
    # The base ref itself must resolve; if it does not, fail rather than skip.
    resolved = _git("rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}")
    if resolved.returncode != 0:
        raise RuntimeError(f"base ref {base_ref!r} could not be resolved for the breaking-change gate")
    # Discriminate "path genuinely absent" from "lookup failed": ls-tree exits 0
    # for a resolvable ref and prints a tree entry only when the path exists. A
    # nonzero status is an error and must fail closed, never skip the gate.
    listed = _git("ls-tree", base_ref, "--", repo_relative, cwd=toplevel)
    if listed.returncode != 0:
        raise RuntimeError(f"failed to query {repo_relative} at {base_ref}: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        # Ref resolves but has no committed artifact — the legitimate first-publication skip.
        return None
    shown = _git("show", f"{base_ref}:{repo_relative}", cwd=toplevel)
    if shown.returncode != 0:
        raise RuntimeError(f"failed to read {repo_relative} at {base_ref}: {shown.stderr.strip()}")
    return shown.stdout


def allowance_path(major: str = API_MAJOR) -> Path:
    """Return the reviewed breaking-change allowance file for an API major."""
    return ARTIFACT_DIR / f"{major}-breaking-allowances.json"


def _load_allowances(major: str = API_MAJOR) -> list[dict[str, Any]]:
    """Return the declared allowances, or an empty list when none are declared."""
    path = allowance_path(major)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _allowance_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    """Return the exact-match key for an allowance or an oasdiff breaking change.

    Keyed on oasdiff's stable ``fingerprint`` plus the human-readable ``id`` and
    ``path``. The fingerprint alone would match, but it is opaque: carrying the
    id and path makes the committed file reviewable and turns a mistyped or
    copy-pasted fingerprint into a miss rather than a silent broad waiver.
    """
    return (str(entry.get("fingerprint", "")), str(entry.get("id", "")), str(entry.get("path", "")))


def _expired_allowances(allowances: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Return allowances whose ``expires_on`` has passed."""
    expired = []
    for entry in allowances:
        raw = entry.get("expires_on")
        if raw and date.fromisoformat(str(raw)) < today:
            expired.append(entry)
    return expired


def _render_breaks(breaks: list[dict[str, Any]]) -> str:
    """Render breaking changes as one reviewable line each."""
    return "\n".join(f"  {b.get('id')} at {b.get('path')}: {b.get('text')} [{b.get('fingerprint')}]" for b in breaks)


def _run_oasdiff(base_text: str, current_text: str) -> subprocess.CompletedProcess[str]:
    """Run the pinned checker over two documents and return its completed process."""
    binary = shutil.which(_OASDIFF_BIN) or _OASDIFF_BIN
    with tempfile.TemporaryDirectory() as tmp:
        # Fixed, literal file names under a private temp dir — the paths never
        # derive from the document contents; only trusted OpenAPI JSON is written
        # through the open file handle.
        base_file = Path(tmp) / "base.json"
        revision_file = Path(tmp) / "revision.json"
        with base_file.open("w", encoding="utf-8") as handle:
            handle.write(base_text)
        with revision_file.open("w", encoding="utf-8") as handle:
            handle.write(current_text)
        return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell; pinned oasdiff binary
            [binary, "breaking", str(base_file), str(revision_file), "--fail-on", "ERR", "-f", "json"],
            capture_output=True,
            text=True,
            check=False,
        )


def _evaluate_report(
    result: subprocess.CompletedProcess[str],
    allowances: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Decide compatibility from a non-clean oasdiff run against the allowances."""
    try:
        reported = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        # oasdiff failed before producing a report (missing binary, unreadable
        # document). Fail closed on its raw output rather than guessing.
        return False, (result.stdout + result.stderr).strip()

    allowed_keys = {_allowance_key(entry) for entry in allowances}
    unallowed = [b for b in reported if _allowance_key(b) not in allowed_keys]
    if unallowed:
        return False, f"{len(unallowed)} breaking change(s) with no ADR-040-R5 allowance:\n{_render_breaks(unallowed)}"

    matched = {_allowance_key(b) for b in reported}
    spent = [e for e in allowances if _allowance_key(e) not in matched]
    detail = f"{len(reported)} breaking change(s), all covered by reviewed ADR-040-R5 allowances."
    if spent:
        listed = "\n".join(f"  {e.get('id')} at {e.get('path')} [{e.get('fingerprint')}]" for e in spent)
        detail += f"\n{len(spent)} spent allowance(s) no longer matching any break; delete them:\n{listed}"
    return True, detail


def check_breaking_changes(
    base_text: str,
    current_text: str,
    allowances: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> tuple[bool, str]:
    """Compare two OpenAPI documents for consumer-breaking changes via oasdiff.

    Returns ``(is_compatible, detail)``. The OpenAPI-aware semantics live in the
    pinned oasdiff binary, not in this wrapper (ADR-040-R2). A breaking change to
    ``/api/v1/`` must ship as a parallel ``/api/v2/`` with a migration note
    (ADR-040-R3) unless it is a reviewed removal of a never-published surface
    declared in the allowance file (ADR-040-R5).

    Allowances are matched exactly, never by pattern: an undeclared break still
    fails. An expired allowance fails. A spent allowance — one that no longer
    matches any reported break, which is the normal state once the base branch
    has caught up — is reported for cleanup but does not fail, so it cannot break
    unrelated pull requests.
    """
    allowances = allowances or []
    expired = _expired_allowances(allowances, today or date.today())
    if expired:
        listed = "\n".join(f"  {e.get('id')} at {e.get('path')} expired {e.get('expires_on')}" for e in expired)
        return False, f"Expired ADR-040-R5 allowance(s); renew with review or remove:\n{listed}"
    result = _run_oasdiff(base_text, current_text)
    if result.returncode == 0:
        return True, (result.stdout + result.stderr).strip()
    return _evaluate_report(result, allowances)


def check_breaking_against(base_ref: str, major: str = API_MAJOR) -> tuple[bool, str]:
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
    return check_breaking_changes(base_text, path.read_text(encoding="utf-8"), _load_allowances(major))
