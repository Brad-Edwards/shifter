"""Guardrail-docs, documentation-coverage, and agent-attribution checks."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .._common import (
    Violation,
    _load_json_yaml,
)


GUARDRAIL_PREFIXES = (
    ".github/workflows/",
    ".claude/hooks/",
    "scripts/adr_guard/",
    "docs/adr/",
)
GUARDRAIL_FILES = {
    ".pre-commit-config.yaml",
    ".ground-control.yaml",
    ".gc/plan-rules.md",
    ".claude/settings.json",
    "AGENTS.md",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".github/copilot-instructions.md",
    ".github/dependabot.yml",
    ".importlinter",
    ".tflint.hcl",
    ".gitleaks.toml",
    ".kube-linter.yaml",
    # Repo-root runtime config seeded by #777 (mcp_ops policy). Changes
    # here can weaken capability classes, profile gating, env defaults,
    # audit redaction, or prod-confirm policy without touching code, so
    # ADR enforcement watches the file.
    ".shifter.yaml",
    ".cursor/cli.json",
}
DOC_PATHS = (
    "docs/adr/",
    "docs/technical/dev/adr-enforcement.md",
    "docs/technical/dev/index.md",
    "docs/technical/index.md",
)


def _is_guardrail_file(path: str) -> bool:
    return path in GUARDRAIL_FILES or any(path.startswith(prefix) for prefix in GUARDRAIL_PREFIXES)


def _is_docs_file(path: str) -> bool:
    return any(path == item or path.startswith(item) for item in DOC_PATHS)


def check_guardrail_docs(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Require documentation updates when guardrails change."""
    if not files:
        return []

    touched_guardrails = [path for path in files if _is_guardrail_file(path)]
    if not touched_guardrails:
        return []

    if any(_is_docs_file(path) for path in files):
        return []

    first_path = touched_guardrails[0]
    return [
        Violation(
            "guardrail-docs",
            "ADR-002-R1",
            first_path,
            "Guardrail changes must update docs/adr or the developer ADR enforcement docs in the same change",
        )
    ]


def check_no_agent_attribution(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Reject AI/agent marketing or co-author attribution in tracked text."""
    from agent_attribution import find_agent_attribution_matches

    candidates = files
    if candidates is None:
        candidates = [
            rel
            for rel in subprocess.check_output(["git", "ls-files"], cwd=repo_root, text=True).splitlines()
            if rel
        ]

    violations: list[Violation] = []
    for rel in candidates:
        if rel in _AGENT_ATTRIBUTION_SCAN_SKIP:
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        matches = find_agent_attribution_matches(text)
        if not matches:
            continue
        first = matches[0]
        violations.append(
            Violation(
                "no-agent-attribution",
                "ADR-002-R2",
                rel,
                f"Prohibited AI/agent attribution ({first.rule}): {first.excerpt}",
            )
        )
    return violations


DOCS_COVERAGE_MANIFEST = "docs/adr/documentation-coverage.yaml"
DOCS_COVERAGE_RULE_ID = "ADR-022-R1"
_AGENT_ATTRIBUTION_SCAN_SKIP = {
    "scripts/adr_guard/agent_attribution.py",
    "scripts/adr_guard/block_agent_attribution_commit_msg.py",
    "scripts/adr_guard/tests/test_agent_attribution.py",
}
_DOCS_EXCLUDED_PART = "_deprecated"
_MARKDOWN_LINK_PATTERN = re.compile(r"\]\(([^)\s]+)")


def _normalize_doc_slug(value: str) -> str:
    """Normalize a posix doc slug, resolving ``.``/``..`` segments."""
    segments: list[str] = []
    for segment in value.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return "/".join(segments)


def _doc_path_is_excluded(slug: str) -> bool:
    """A doc under a ``_deprecated`` or hidden path part is not serveable."""
    return any(part == _DOCS_EXCLUDED_PART or part.startswith(".") for part in slug.split("/") if part)


def _collect_index_link_slugs(docs_root: Path) -> set[str]:
    """Return the set of docs-root-relative slugs linked from any index.md."""
    linked: set[str] = set()
    if not docs_root.is_dir():
        return linked
    for index_file in docs_root.rglob("index.md"):
        rel_parts = index_file.relative_to(docs_root).parts
        if any(part == _DOCS_EXCLUDED_PART or part.startswith(".") for part in rel_parts):
            continue
        index_dir = "/".join(rel_parts[:-1])
        try:
            text = index_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for target in _MARKDOWN_LINK_PATTERN.findall(text):
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "/")):
                continue
            if target.endswith(".md"):
                target = target[: -len(".md")]
            linked.add(_normalize_doc_slug(f"{index_dir}/{target}"))
    return linked


def check_documentation_coverage(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Require every major feature to carry user and technical documentation.

    The coverage manifest (``docs/adr/documentation-coverage.yaml``) is the
    source of truth, so the check validates the whole manifest on every run
    regardless of ``files`` (like ``check_adr_registry``). Each feature must
    declare at least one user doc and one technical doc; every referenced doc
    must exist as a serveable file under the in-app docs tree (not under a
    ``_deprecated``/hidden path) and be reachable from an ``index.md``.
    """
    manifest_path = repo_root / DOCS_COVERAGE_MANIFEST
    try:
        manifest = _load_json_yaml(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        return [Violation("documentation-coverage", DOCS_COVERAGE_RULE_ID, DOCS_COVERAGE_MANIFEST, str(err))]

    if not isinstance(manifest, dict):
        return [
            Violation(
                "documentation-coverage",
                DOCS_COVERAGE_RULE_ID,
                DOCS_COVERAGE_MANIFEST,
                "documentation coverage manifest must be a JSON object",
            )
        ]

    docs_root_rel = manifest.get("docs_root")
    features = manifest.get("features")
    if not isinstance(docs_root_rel, str) or not isinstance(features, list):
        return [
            Violation(
                "documentation-coverage",
                DOCS_COVERAGE_RULE_ID,
                DOCS_COVERAGE_MANIFEST,
                "manifest must define a string 'docs_root' and a list of 'features'",
            )
        ]

    docs_root = repo_root / docs_root_rel
    linked_slugs = _collect_index_link_slugs(docs_root)
    violations: list[Violation] = []

    for feature in features:
        if not isinstance(feature, dict):
            violations.append(
                Violation(
                    "documentation-coverage",
                    DOCS_COVERAGE_RULE_ID,
                    DOCS_COVERAGE_MANIFEST,
                    "each feature entry must be a JSON object",
                )
            )
            continue
        feature_id = feature.get("id") or "<unknown>"
        user_docs = feature.get("user_docs") or []
        technical_docs = feature.get("technical_docs") or []
        if not user_docs:
            violations.append(
                Violation(
                    "documentation-coverage",
                    DOCS_COVERAGE_RULE_ID,
                    DOCS_COVERAGE_MANIFEST,
                    f"feature {feature_id} must declare at least one user doc",
                )
            )
        if not technical_docs:
            violations.append(
                Violation(
                    "documentation-coverage",
                    DOCS_COVERAGE_RULE_ID,
                    DOCS_COVERAGE_MANIFEST,
                    f"feature {feature_id} must declare at least one technical doc",
                )
            )
        for rel in list(user_docs) + list(technical_docs):
            slug = _normalize_doc_slug(rel)
            doc_path = f"{docs_root_rel}/{slug}"
            if _doc_path_is_excluded(slug):
                violations.append(
                    Violation(
                        "documentation-coverage",
                        DOCS_COVERAGE_RULE_ID,
                        doc_path,
                        f"feature {feature_id} references a deprecated or hidden doc that is not served",
                    )
                )
                continue
            if not (docs_root / slug).is_file():
                violations.append(
                    Violation(
                        "documentation-coverage",
                        DOCS_COVERAGE_RULE_ID,
                        doc_path,
                        f"feature {feature_id} references a missing doc",
                    )
                )
                continue
            doc_slug = slug[: -len(".md")] if slug.endswith(".md") else slug
            if doc_slug not in linked_slugs:
                violations.append(
                    Violation(
                        "documentation-coverage",
                        DOCS_COVERAGE_RULE_ID,
                        doc_path,
                        f"feature {feature_id} references an orphaned doc not linked from any index.md",
                    )
                )

    return violations
