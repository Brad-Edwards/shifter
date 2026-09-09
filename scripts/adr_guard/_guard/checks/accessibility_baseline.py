"""Accessibility exact-finding baseline non-growth ratchet (ADR-055-R4/R6)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .._common import (
    Violation,
    _boundary_mock_base_reference_candidates,
    _git_text,
    exception_is_active,
    load_adr_exceptions,
)

_A11Y_BASELINE_REL = "shifter/shifter_platform/frontend/e2e/a11y/baseline.json"
_A11Y_CHECK = "accessibility-baseline"
_A11Y_RULE = "ADR-055-R4"
# CI lanes that fetch base-branch history set this so the ratchet fails CLOSED
# when it cannot read the base; local/shallow runs fail open (mirrors ADR-011-R8).
_A11Y_ENFORCE_ENV = "ADR_GUARD_SNAPSHOT_ENFORCE"


def _a11y_enforced() -> bool:
    return os.environ.get(_A11Y_ENFORCE_ENV, "").strip().lower() in {"1", "true", "yes"}


def _a11y_violation(message: str) -> Violation:
    return Violation(_A11Y_CHECK, _A11Y_RULE, _A11Y_BASELINE_REL, message)


def _a11y_unverifiable(enforce: bool, message: str) -> list[Violation]:
    return [_a11y_violation(message)] if enforce else []


def _parse_baseline(raw: str) -> set[str] | None:
    """Parse a baseline JSON array of fingerprint strings; None if malformed."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        return None
    return set(data)


def _a11y_waived_fingerprints(repo_root: Path) -> set[str]:
    """Union of exact fingerprints waived by active ADR-055 exceptions (R6)."""
    try:
        exceptions = load_adr_exceptions(repo_root)
    except (OSError, ValueError):
        return set()
    waived: set[str] = set()
    for exception in exceptions:
        rule_id = exception.get("rule_id")
        if not (isinstance(rule_id, str) and rule_id.startswith("ADR-055")):
            continue
        if not exception_is_active(exception):
            continue
        fingerprints = exception.get("fingerprints") or []
        if isinstance(fingerprints, list):
            waived.update(fp for fp in fingerprints if isinstance(fp, str))
    return waived


def check_accessibility_baseline(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """The ADR-055 exact-finding accessibility baseline may only shrink (R4).

    The committed fingerprint set must be a subset of the trusted base-branch
    baseline: new fingerprints (baseline growth) are forbidden unless covered by
    an exact-fingerprint waiver in ``docs/adr/exceptions.yaml`` naming ADR-055
    (R6). Resolved findings (removed from the baseline) are always allowed.

    Fail-open by default (a shallow clone cannot compare, so local dev is not
    blocked) and fail-CLOSED under ``ADR_GUARD_SNAPSHOT_ENFORCE`` (the CI lane
    sets it and fetches base history), mirroring the published-contract ratchet.
    The per-surface exact-set comparison against the live scan is enforced by the
    Playwright accessibility spec; this check enforces cross-PR non-growth of the
    committed baseline.
    """
    # global repository invariant, not scoped to the changed-file set
    del files
    enforce = _a11y_enforced()
    head_path = repo_root / _A11Y_BASELINE_REL
    if not head_path.exists():
        # No committed baseline: the scan asserts findings against an implicit
        # empty set, so there is nothing to ratchet.
        return []
    head = _parse_baseline(head_path.read_text(encoding="utf-8"))
    if head is None:
        return [_a11y_violation("committed accessibility baseline is not a JSON array of fingerprint strings")]

    base_refs = _boundary_mock_base_reference_candidates(repo_root)
    if not base_refs:
        return _a11y_unverifiable(
            enforce,
            "cannot resolve a base ref to verify the accessibility baseline did not grow; "
            "the CI lane must fetch base-branch history (fetch-depth: 0)",
        )
    base_raw = _git_text(repo_root, ["show", f"{base_refs[0]}:{_A11Y_BASELINE_REL}"])
    if base_raw is None:
        # No baseline on the base branch yet: initial enrollment. Enrollment
        # purity (no product/template/route/stylesheet change) is a reviewed
        # ADR-055-R4 constraint, not re-derived here.
        return []
    base = _parse_baseline(base_raw)
    if base is None:
        return _a11y_unverifiable(enforce, "cannot parse the base-branch accessibility baseline to verify non-growth")

    added = head - base
    if not added:
        return []
    unwaived = sorted(added - _a11y_waived_fingerprints(repo_root))
    if not unwaived:
        return []
    shown = "; ".join(unwaived[:5]) + ("; ..." if len(unwaived) > 5 else "")
    return [
        _a11y_violation(
            f"accessibility baseline grew by {len(unwaived)} un-waived fingerprint(s); additions are "
            "forbidden (fix the finding or add an exact-fingerprint waiver in docs/adr/exceptions.yaml "
            f"naming ADR-055): {shown}"
        )
    ]
