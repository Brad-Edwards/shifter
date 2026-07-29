"""Boundary-mock non-growth policy (ADR-019-R1)."""
from __future__ import annotations

import ast
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .._common import (
    Violation,
    _boundary_mock_base_reference_candidates,
    _boundary_mock_fallback_reference_candidates,
    _git_text,
    _repo_relative,
    is_guard_source_path,
)


@dataclass(frozen=True)
class _BoundaryPatchSite:
    """One statically discovered mock patch target."""

    path: str
    line: int
    target: str


_BOUNDARY_MOCK_BASELINE_PATH = "scripts/adr_guard/boundary_mock_baseline.json"
_BOUNDARY_MOCK_CHECK_NAME = "boundary-mock-policy"
_BOUNDARY_MOCK_RULE = "ADR-019-R1"
_BOUNDARY_MOCK_SKIP_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "staticfiles",
        "venv",
    }
)
_BOUNDARY_MOCK_BOUNDARY_SEGMENTS = frozenset(
    {
        "boto3",
        "botocore",
        "channels",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "subprocess",
        "urllib",
    }
)


def _boundary_mock_violation(path: str, message: str) -> Violation:
    """Shorthand for ADR-019-R1 violations."""
    return Violation(_BOUNDARY_MOCK_CHECK_NAME, _BOUNDARY_MOCK_RULE, path, message)


def _has_boundary_mock_skip_part(rel_path: str) -> bool:
    """Return True for files under local caches, virtualenvs, or generated trees."""
    return any(part in _BOUNDARY_MOCK_SKIP_PARTS for part in Path(rel_path).parts)


def _is_boundary_mock_test_path(rel_path: str) -> bool:
    """Return True for Python test files scanned by the boundary-mock policy."""
    if not rel_path.endswith(".py") or _has_boundary_mock_skip_part(rel_path):
        return False
    path = Path(rel_path)
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _git_tracked_python_files(repo_root: Path) -> list[str] | None:
    """Return tracked + non-ignored Python files, or None outside a git worktree."""
    if not (repo_root / ".git").exists():
        return None
    cmd = [
        "git",
        "-C",
        str(repo_root),
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        "*.py",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry]


def _walk_python_files(repo_root: Path) -> list[str]:
    """Filesystem fallback for synthetic tests without a git index."""
    files: list[str] = []
    for path in repo_root.rglob("*.py"):
        rel = _repo_relative(path, repo_root)
        if _has_boundary_mock_skip_part(rel):
            continue
        files.append(rel)
    return sorted(files)


def _iter_repo_python_files(repo_root: Path) -> list[str]:
    """Return repo-relative Python files from git when available."""
    tracked = _git_tracked_python_files(repo_root)
    if tracked is not None:
        return sorted({p for p in tracked if not _has_boundary_mock_skip_part(p)})
    return _walk_python_files(repo_root)


def _first_party_python_roots(repo_root: Path) -> set[str]:
    """Infer first-party import roots from tracked Python modules and packages."""
    roots: set[str] = set()
    for rel in _iter_repo_python_files(repo_root):
        if _is_boundary_mock_test_path(rel):
            continue
        path = Path(rel)
        if path.name == "__init__.py":
            root = path.parent.name
        else:
            root = path.stem
        if not root.isidentifier() or root in {"conftest", "tests"} or root.startswith("test_"):
            continue
        roots.add(root)
    return roots


def _boundary_mock_scope(repo_root: Path, files: list[str] | None) -> list[str]:
    """Select test files to scan for this invocation."""
    if files is None:
        return [p for p in _iter_repo_python_files(repo_root) if _is_boundary_mock_test_path(p)]

    touched = set(files)
    if _BOUNDARY_MOCK_BASELINE_PATH in touched or any(is_guard_source_path(t) for t in touched):
        return [p for p in _iter_repo_python_files(repo_root) if _is_boundary_mock_test_path(p)]

    return sorted({p for p in files if _is_boundary_mock_test_path(p)})


def _name_chain(node: ast.AST) -> str | None:
    """Return a dotted name for simple Name/Attribute AST nodes."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name_chain(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _resolve_imported_name(name: str, imported_modules: dict[str, str]) -> str | None:
    """Resolve the leading segment of a dotted name through import aliases."""
    head, sep, tail = name.partition(".")
    resolved = imported_modules.get(head)
    if resolved is None:
        return name
    return f"{resolved}.{tail}" if sep else resolved


def _collect_mock_aliases(tree: ast.AST) -> tuple[set[str], set[str], dict[str, str]]:
    """Collect unittest.mock aliases and imported module aliases from a file."""
    patch_names: set[str] = set()
    mock_modules: set[str] = set()
    imported_modules: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                imported_modules[local] = alias.name
                if alias.name == "unittest":
                    mock_modules.add(f"{local}.mock")
                elif alias.name == "unittest.mock":
                    mock_modules.add(local if alias.asname else "unittest.mock")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                imported_modules[local] = f"{module}.{alias.name}" if module else alias.name
                if module == "unittest.mock" and alias.name == "patch":
                    patch_names.add(local)
                elif module == "unittest" and alias.name == "mock":
                    mock_modules.add(local)

    return patch_names, mock_modules, imported_modules


def _is_mock_patch_func(func: ast.AST, patch_names: set[str], mock_modules: set[str]) -> bool:
    """Return True for patch(...) or mock/mocker.patch(...)."""
    if isinstance(func, ast.Name):
        return func.id in patch_names
    if isinstance(func, ast.Attribute) and func.attr == "patch":
        base = _name_chain(func.value)
        return base in mock_modules or base == "mocker"
    return False


def _is_mock_patch_object_func(func: ast.AST, patch_names: set[str], mock_modules: set[str]) -> bool:
    """Return True for patch.object(...) or mock/mocker.patch.object(...)."""
    return isinstance(func, ast.Attribute) and func.attr == "object" and _is_mock_patch_func(
        func.value, patch_names, mock_modules
    )


def _patch_object_target(call: ast.Call, imported_modules: dict[str, str]) -> str | None:
    """Resolve patch.object(module_or_class, "name") into a dotted target when static."""
    if len(call.args) < 2:
        return None
    attr_arg = call.args[1]
    if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
        return None
    base = _name_chain(call.args[0])
    if base is None:
        return None
    resolved = _resolve_imported_name(base, imported_modules)
    if resolved is None:
        return None
    return f"{resolved}.{attr_arg.value}"


def _iter_boundary_patch_sites(repo_root: Path, rel_paths: list[str]) -> list[_BoundaryPatchSite]:
    """Statically discover string patch targets in selected test files."""
    sites: list[_BoundaryPatchSite] = []
    for rel in rel_paths:
        path = repo_root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        patch_names, mock_modules, imported_modules = _collect_mock_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target: str | None = None
            if (
                _is_mock_patch_func(node.func, patch_names, mock_modules)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                target = node.args[0].value
            elif _is_mock_patch_object_func(node.func, patch_names, mock_modules):
                target = _patch_object_target(node, imported_modules)

            if target:
                sites.append(_BoundaryPatchSite(rel, node.lineno, target))
    return sites


def _is_allowed_boundary_patch_target(target: str) -> bool:
    """Return True for patch targets aimed at process/network/cloud boundaries."""
    parts = target.split(".")
    return any(part in _BOUNDARY_MOCK_BOUNDARY_SEGMENTS for part in parts[1:])


def _is_first_party_internal_patch_target(target: str, first_party_roots: set[str]) -> bool:
    """Return True for first-party targets that are not explicit boundary adapters."""
    root = target.split(".", 1)[0]
    return root in first_party_roots and not _is_allowed_boundary_patch_target(target)


def _parse_boundary_mock_baseline(raw: str, source: str) -> tuple[Counter[tuple[str, str]], Violation | None]:
    """Parse a boundary mock baseline payload into counts keyed by (path, target)."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Counter(), _boundary_mock_violation(
            _BOUNDARY_MOCK_BASELINE_PATH,
            f"invalid baseline JSON in {source}: {exc}",
        )

    records = payload.get("allowed_internal_patch_counts") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return Counter(), _boundary_mock_violation(
            _BOUNDARY_MOCK_BASELINE_PATH,
            f"baseline in {source} must contain an allowed_internal_patch_counts list",
        )

    counts: Counter[tuple[str, str]] = Counter()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            return Counter(), _boundary_mock_violation(
                _BOUNDARY_MOCK_BASELINE_PATH,
                f"baseline entry {index} in {source} must be an object",
            )
        rel = record.get("path")
        target = record.get("target")
        count = record.get("count")
        if not isinstance(rel, str) or not isinstance(target, str) or not isinstance(count, int) or count < 0:
            return Counter(), _boundary_mock_violation(
                _BOUNDARY_MOCK_BASELINE_PATH,
                f"baseline entry {index} in {source} must have string path/target "
                "and non-negative integer count",
            )
        counts[(rel, target)] += count
    return counts, None


def _load_boundary_mock_baseline(repo_root: Path) -> tuple[Counter[tuple[str, str]], Violation | None]:
    """Load the working-tree legacy internal patch baseline."""
    path = repo_root / _BOUNDARY_MOCK_BASELINE_PATH
    if not path.exists():
        return Counter(), _boundary_mock_violation(
            _BOUNDARY_MOCK_BASELINE_PATH,
            "boundary mock baseline is missing; generate it from current legacy internal patch counts",
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Counter(), _boundary_mock_violation(
            _BOUNDARY_MOCK_BASELINE_PATH,
            f"could not read baseline: {exc}",
        )

    return _parse_boundary_mock_baseline(raw, "working tree")


def _load_boundary_mock_reference_baseline(
    repo_root: Path,
) -> tuple[Counter[tuple[str, str]] | None, Violation | None]:
    """Load the baseline from the branch reference point, when one exists."""
    base_refs = _boundary_mock_base_reference_candidates(repo_root)
    for ref in base_refs:
        raw = _git_text(repo_root, ["show", f"{ref}:{_BOUNDARY_MOCK_BASELINE_PATH}"])
        if raw is None:
            continue
        return _parse_boundary_mock_baseline(raw, f"git reference {ref}")
    if base_refs:
        return None, None

    for ref in _boundary_mock_fallback_reference_candidates(repo_root):
        raw = _git_text(repo_root, ["show", f"{ref}:{_BOUNDARY_MOCK_BASELINE_PATH}"])
        if raw is None:
            continue
        return _parse_boundary_mock_baseline(raw, f"git reference {ref}")
    return None, None


def _check_boundary_mock_baseline_non_growth(
    repo_root: Path,
    current_baseline: Counter[tuple[str, str]],
) -> list[Violation]:
    """Fail any committed baseline allowance that grows against the reference baseline."""
    reference_baseline, reference_error = _load_boundary_mock_reference_baseline(repo_root)
    if reference_error is not None:
        return [reference_error]
    if reference_baseline is None:
        return []

    violations: list[Violation] = []
    for key, allowed in sorted(current_baseline.items()):
        reference_allowed = reference_baseline.get(key, 0)
        if allowed <= reference_allowed:
            continue
        rel, target = key
        violations.append(
            _boundary_mock_violation(
                _BOUNDARY_MOCK_BASELINE_PATH,
                f"baseline allowance for first-party internal patch target {target!r} in {rel} "
                f"grew from {reference_allowed} to {allowed}; baseline counts may only shrink "
                "without a dated ADR exception",
            )
        )
    return violations


def check_boundary_mock_policy(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Prevent net-new first-party internal mock patch targets in tests.

    Existing topology-coupled tests are represented by a committed baseline of
    ``(test file, patch target) -> count``. The check allows the baseline to
    shrink but fails any new internal target or count increase. Process,
    network, cloud SDK, and channel-layer transport patch targets remain
    allowed because they are real boundaries rather than first-party topology.
    """
    scan_files = _boundary_mock_scope(repo_root, files)
    if not scan_files:
        return []

    baseline, baseline_error = _load_boundary_mock_baseline(repo_root)
    if baseline_error is not None:
        return [baseline_error]

    violations = _check_boundary_mock_baseline_non_growth(repo_root, baseline)
    first_party_roots = _first_party_python_roots(repo_root)
    current: Counter[tuple[str, str]] = Counter()
    first_line: dict[tuple[str, str], int] = {}
    for site in _iter_boundary_patch_sites(repo_root, scan_files):
        if not _is_first_party_internal_patch_target(site.target, first_party_roots):
            continue
        key = (site.path, site.target)
        current[key] += 1
        first_line.setdefault(key, site.line)

    for key, found in sorted(current.items()):
        allowed = baseline.get(key, 0)
        if found <= allowed:
            continue
        rel, target = key
        violations.append(
            _boundary_mock_violation(
                f"{rel}:{first_line[key]}",
                f"first-party internal patch target {target!r} exceeds the legacy baseline "
                f"(allowed {allowed}, found {found}); patch a process/network/cloud boundary "
                "or assert observable behavior instead",
            )
        )
    return violations
