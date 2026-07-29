"""Secret/artifact hygiene: tfvars secrets, secret env files, tracked generated artifacts."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .._common import (
    Violation,
    _repo_relative,
)


_TFVARS_SCOPE = (
    "platform/terraform/environments",
    "platform/terraform/global",
)
_SECRET_NAME_GROUP = (
    r"((?:[A-Za-z_][A-Za-z0-9_]*"
    r"(?:_passwords?|_secrets?|_tokens?|_keys?|_credentials?|_authcodes?|_pin_values?))"
    r"|(?:authcodes?|pin_values?))"
)
_SECRET_VAR_PATTERN = re.compile(
    r"^\s*" + _SECRET_NAME_GROUP + r'\s*=\s*"[^"]+"',
)
# HCL also supports heredoc string literals (`name = <<EOF` /
# `name = <<-EOF`), which would otherwise bypass the line regex above.
_SECRET_HEREDOC_PATTERN = re.compile(
    r"^\s*" + _SECRET_NAME_GROUP + r"\s*=\s*<<-?[A-Za-z_][A-Za-z0-9_]*\s*$",
)
# Object / array assignments to secret-bearing variables. These are
# walked forward to the matching brace/bracket and flagged when any
# string literal appears inside.
_SECRET_BLOCK_OPEN_PATTERN = re.compile(
    r"^\s*" + _SECRET_NAME_GROUP + r"\s*=\s*([\{\[])",
)
# Generic single-line assignment to a secret-bearing variable. Used to
# catch function-wrapped string literals like
# `db_password = trimspace("...")` or `api_token = sensitive("...")`
# that the bare-string and block-open patterns above don't cover. The
# RHS is whatever follows `=` on the same line; the violation walker
# then scans for a string literal in that RHS (after stripping trailing
# # / // comments) and flags when present.
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"^\s*" + _SECRET_NAME_GROUP + r"\s*=\s*(.+)$",
)
_STRING_LITERAL_PATTERN = re.compile(r'"[^"]+"')
_BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)
# Variable-name suffixes that mark share-only material (SSH/JWT public
# keys, authorized_keys files, public certificates) so the suffix-based
# regex doesn't over-flag them. Matched against `var_name.endswith(...)`
# so a variable like `public_key_password` is NOT exempted (the secret
# suffix `_password` still wins, even though `public_key` appears in
# the name).
_NON_SECRET_NAME_SUFFIXES = (
    "_public_key",
    "_public_keys",
    "_public_cert",
    "_public_certs",
    "_pub_key",
    "_pub_keys",
    "_pubkey",
    "_pubkeys",
    "_authorized_keys",
    "public_key",
    "public_keys",
    "public_cert",
    "public_certs",
    "pub_key",
    "pub_keys",
    "pubkey",
    "pubkeys",
    "authorized_keys",
)


def _strip_hcl_comments(text: str) -> str:
    """Replace HCL block comments with whitespace (preserving newlines).

    Line comments (`#`, `//`) are handled per-line by the caller so it can
    keep line numbers aligned for violation reporting. Block comments are
    stripped here because they can span lines; we replace each character
    with whitespace except newlines so subsequent regexes still see the
    same line numbers.
    """

    def _blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return _BLOCK_COMMENT_PATTERN.sub(_blank, text)


def _is_line_commented(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("#", "//"))


def _strip_trailing_line_comment(line: str) -> str:
    """Drop trailing `#` or `//` line-comment tail from an HCL line.

    Walks the line keeping track of whether we're inside a `"..."`
    string so a `#` or `//` inside a string is preserved.
    """
    in_string = False
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "#":
                return line[:i]
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                return line[:i]
        i += 1
    return line


def _balance_scan(chars: str, depth: int) -> tuple[int, bool, bool]:
    """Scan ``chars`` updating the ``()``/``[]``/``{}`` ``depth``.

    Returns ``(new_depth, saw_delimiter, closed_to_zero)``. ``closed_to_zero``
    is ``True`` the moment depth drops to ``<= 0`` — the expression closed
    within ``chars``.
    """
    saw = False
    for ch in chars:
        if ch in "([{":
            depth += 1
            saw = True
        elif ch in ")]}":
            depth -= 1
            saw = True
            if depth <= 0:
                return depth, saw, True
    return depth, saw, False


def _block_depth_scan(chars: str, depth: int, opener: str, closer: str) -> tuple[int, bool]:
    """Scan ``chars`` updating the ``opener``/``closer`` ``depth``.

    Returns ``(new_depth, closed_to_zero)``; ``closed_to_zero`` is ``True``
    the moment depth returns to ``0``.
    """
    for ch in chars:
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return depth, True
    return depth, False


def _scrub_line(line: str) -> str:
    """Blank out ``"..."`` string contents and drop the trailing ``#``/``//``
    line comment so brace/paren counting ignores both.
    """
    return _strip_trailing_line_comment(_STRING_LITERAL_PATTERN.sub('""', line))


def _find_balanced_close_index(lines: list[str], start_idx: int, start_pos: int) -> int | None:
    """Walk forward from ``lines[start_idx][start_pos:]`` tracking the
    running depth of ``()``/``[]``/``{}`` (string-literal- and line-comment-aware)
    until the depth returns to zero. Returns the line index containing
    the closing delimiter, or ``None`` if no balance by end-of-file. A start
    line with no delimiter at all is treated as the close (nothing to balance).

    Used by the wrapped-expression arm of the secrets check so multi-line
    wrappers like ``db_password = jsonencode({\\n  password = "leak"\\n})``
    are walked across newlines and scanned for inner string literals.
    """
    depth = 0
    started = False
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if _is_line_commented(line):
            continue
        offset = start_pos if idx == start_idx else 0
        depth, saw, closed = _balance_scan(_scrub_line(line)[offset:], depth)
        if saw:
            started = True
        if closed:
            return idx
        if not started:
            return idx  # no opener on the start line — nothing to balance
    return None


def _find_block_close_index(lines: list[str], start_idx: int, opener: str) -> int | None:
    """Return the line index containing the brace/bracket that closes the block.

    ``opener`` is ``"{"`` or ``"["``; matched closer is ``"}"`` / ``"]"``. The
    walk treats string literals and ``#`` / ``//`` line comments as inert
    (their contents don't change the brace count). Returns the matching line
    index, or ``None`` if no close is found by end-of-file.
    """
    closer = "}" if opener == "{" else "]"
    depth = 0
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if _is_line_commented(line):
            continue
        depth, closed = _block_depth_scan(_scrub_line(line), depth, opener, closer)
        if closed:
            return idx
    return None


def _collect_tfvars_candidates(repo_root: Path, files: list[str] | None) -> list[Path]:
    """Resolve the ``*.tfvars`` files in scope: the subset of ``files`` that
    sits under ``platform/terraform/environments/`` when an explicit list is
    given, otherwise every ``*.tfvars`` file under that tree.
    """
    if files is not None:
        in_scope = [p for p in files if p.startswith(_TFVARS_SCOPE) and p.endswith(".tfvars")]
        return [repo_root / p for p in in_scope]
    candidates: list[Path] = []
    for scope in _TFVARS_SCOPE:
        base = repo_root / scope
        if not base.exists():
            continue
        candidates.extend(p for p in base.rglob("*.tfvars") if p.is_file() and not p.is_symlink())
    return candidates


def _is_public_material_name(var_name: str) -> bool:
    """``True`` for names ending in a public-material suffix (``*_public_key``,
    ``*_authorized_keys``, ``*_pubkey``, …) — material that is share-only by
    design, so a string literal there is not a leaked secret.
    """
    return any(var_name.endswith(suffix) for suffix in _NON_SECRET_NAME_SUFFIXES)


def _lines_have_string_literal(lines: list[str], start_idx: int, end_idx: int) -> bool:
    """``True`` if any line in ``lines[start_idx:end_idx + 1]`` carries a
    ``"..."`` literal once full-line comments are skipped and trailing
    ``#``/``//`` comment tails are stripped.
    """
    for idx in range(start_idx, end_idx + 1):
        inner = lines[idx]
        if _is_line_commented(inner):
            continue
        if _STRING_LITERAL_PATTERN.search(_strip_trailing_line_comment(inner)):
            return True
    return False


def _wrapped_rhs_has_literal(lines: list[str], idx: int, line: str, rhs: str) -> bool:
    """``True`` if a function-wrapped / expression RHS of a secret assignment
    materializes a string literal — scanning the RHS on the assignment line
    and, when it opens a balanced ``()``/``[]``/``{}`` that spans lines, the
    rest of the multi-line expression.
    """
    if _STRING_LITERAL_PATTERN.search(_strip_trailing_line_comment(rhs)):
        return True
    close_idx = _find_balanced_close_index(lines, idx, line.find("=") + 1)
    if close_idx is None or close_idx <= idx:
        return False
    return _lines_have_string_literal(lines, idx + 1, close_idx)


def _block_assignment_has_literal(lines: list[str], idx: int, opener: str) -> bool:
    """``True`` if the object/array block opened on ``lines[idx]`` carries a
    string literal somewhere between the opener and its matching close (an
    empty block, or one composed solely of var/local/data references, is
    acceptable). A block whose close is never found scans to end-of-file.
    """
    close_idx = _find_block_close_index(lines, idx, opener)
    end_idx = close_idx if close_idx is not None else len(lines) - 1
    return _lines_have_string_literal(lines, idx, end_idx)


def _flagged_secret_var(lines: list[str], idx: int) -> str | None:
    """Return the secret-bearing variable on ``lines[idx]`` that is assigned a
    plaintext string literal — directly, via a heredoc, via an object/array
    block, or wrapped in a function/expression — or ``None`` when the line is
    clean or the variable name is public material.
    """
    line = lines[idx]
    # Priority: a direct ``= "..."`` / heredoc literal, then an object/array
    # block, then any other RHS expression. ``_SECRET_ASSIGNMENT_PATTERN`` is
    # the catch-all, so it is consulted last.
    direct = _SECRET_VAR_PATTERN.match(line) or _SECRET_HEREDOC_PATTERN.match(line)
    block_match = _SECRET_BLOCK_OPEN_PATTERN.match(line)
    wrapped = _SECRET_ASSIGNMENT_PATTERN.match(line)
    match = direct or block_match or wrapped
    if match is None:
        return None
    var_name = match.group(1)
    if _is_public_material_name(var_name):
        return None
    if direct is not None:
        return var_name
    if block_match is not None:
        return var_name if _block_assignment_has_literal(lines, idx, block_match.group(2)) else None
    return var_name if _wrapped_rhs_has_literal(lines, idx, line, wrapped.group(2)) else None


def _scan_tfvars_file(path: Path, repo_root: Path) -> list[Violation]:
    """Scan one ``*.tfvars`` file for plaintext-secret assignments (ADR-004-R7).

    Block comments are spanned BEFORE line splitting so their contents
    (including any ``password = "..."`` examples) cannot trigger the regex;
    line numbers are preserved.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = _strip_hcl_comments(raw_text).splitlines()
    rel = _repo_relative(path, repo_root)
    violations: list[Violation] = []
    for idx, line in enumerate(lines):
        if _is_line_commented(line):
            continue
        var_name = _flagged_secret_var(lines, idx)
        if var_name is None:
            continue
        violations.append(
            Violation(
                "no-plaintext-secrets-in-tfvars",
                "ADR-004-R7",
                rel,
                f"Line {idx + 1}: {var_name!r} is assigned a "
                f"plaintext string literal; reference an out-of-band "
                f"secret store (Secrets Manager, SSM, environment) instead",
            )
        )
    return violations


def check_no_plaintext_secrets_in_tfvars(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Forbid string literals on secret-bearing tfvars assignments (ADR-004-R7).

    Scans ``*.tfvars`` files committed under the Terraform environment and
    global trees and flags any line that assigns a quoted string to a variable
    whose name ends in ``password``, ``secret``, ``token``, ``key``,
    ``credentials``, ``credential``, ``authcode``, ``authcodes``, or
    ``pin_value``. Bare ``authcode`` / ``pin_value`` names are also flagged.
    Var/local/data references and empty strings are allowed
    (they don't materialize a credential in source). ``*.tfvars.example`` files
    and full-line comments are skipped.

    gitleaks catches high-entropy random strings; this is the complementary
    backstop for low-entropy committed credentials that gitleaks ignores
    (e.g. human-typed passwords with mixed case and a single digit suffix).
    """
    violations: list[Violation] = []
    for path in _collect_tfvars_candidates(repo_root, files):
        if path.exists():
            violations.extend(_scan_tfvars_file(path, repo_root))
    return violations


# Centralized blocked-path / blocked-name set for the
# `no-tracked-generated-artifacts` check (ADR-004-R8). Each entry is a
# pair: (root prefix under which the rule applies, predicate over the
# repo-relative path's basename). The roots are intentionally narrow so
# unrelated source files with overlapping names elsewhere in the repo
# are not flagged.
#
# Terraform plan outputs: `tfplan`, `plan.out`, and any `*.tfplan` /
# `*.tfplan.binary` under the AWS or GCP terraform environment trees.
# These are generated security-sensitive artifacts; they may carry
# state-derived values, resource addresses, and provider metadata and
# must not be tracked in source.
#
# Bootstrap license/authcode material: `authcodes` (and `*.authcodes`)
# under `temp/bootstrap/`. These are pre-staging outputs from local
# bootstrap workflows and must not be committed.
#
# Polaris range build output: every tracked file under
# `scenario-dev/polaris/build/`. That tree is generated/runtime material and
# can carry challenge-local keys, tokens, database access files, and baked
# runtime payloads. Source inputs live outside `build/`.
#
# Polaris AWS operator run outputs: machine-readable provisioning state and
# human-readable status/health reports under `scripts/polaris-aws-range/`.
# These are regenerated by orchestrate_provisioning.py and
# check_range_health.py during live events and may contain participant or
# infrastructure identifiers.
_GENERATED_ARTIFACT_ROOTS: tuple[str, ...] = (
    "platform/terraform/environments/",
    "platform/terraform/gcp/environments/",
    "scenario-dev/polaris/build/",
    "scripts/polaris-aws-range/",
    "temp/bootstrap/",
)


def _is_terraform_plan_artifact(basename: str) -> bool:
    """Return True for Terraform plan output filenames.

    Matches the canonical names produced by `terraform plan -out=...`
    workflows: `tfplan` and `tfplan.binary` (binary plan files) and
    `plan.out` (typical text dump). Also matches the `*.tfplan` and
    `*.tfplan.binary` families so per-environment names like
    `dev.tfplan` and `prod.tfplan.binary` are caught. Case-sensitive
    to avoid over-matching unrelated source filenames such as
    `terraform_planner.py`.
    """
    if basename in ("tfplan", "tfplan.binary", "plan.out"):
        return True
    return basename.endswith((".tfplan", ".tfplan.binary"))


def _is_bootstrap_authcode_artifact(basename: str) -> bool:
    """Return True for tracked bootstrap license/authcode filenames."""
    return basename == "authcodes" or basename.endswith(".authcodes")


def _is_polaris_operator_run_artifact(basename: str) -> bool:
    """Return True for tracked Polaris AWS operator run outputs."""
    return basename in (
        "provisioning_state.json",
        "provisioning_status.md",
        "health_report.md",
        "postprovision_status.md",
    )


def _generated_artifact_match(rel_path: str) -> bool:
    """Return True if a repo-relative path is a blocked generated artifact."""
    in_scope = any(rel_path.startswith(root) for root in _GENERATED_ARTIFACT_ROOTS)
    if not in_scope:
        return False
    basename = rel_path.rsplit("/", 1)[-1]
    if rel_path.startswith("platform/terraform/"):
        return _is_terraform_plan_artifact(basename)
    if rel_path.startswith("scenario-dev/polaris/build/"):
        return True
    if rel_path.startswith("scripts/polaris-aws-range/"):
        return _is_polaris_operator_run_artifact(basename)
    if rel_path.startswith("temp/bootstrap/"):
        return _is_bootstrap_authcode_artifact(basename)
    return False


def _iter_artifact_candidates(repo_root: Path) -> list[str]:
    """Return repo-relative paths of TRACKED files matching the policy.

    Codex review #1180 cycle 1 finding 1: the previous walk-the-
    filesystem implementation flagged any ignored local workspace
    file under the Terraform/temp roots, which would break
    `adr_guard --all --level ci` when a developer or earlier CI step
    generated an ephemeral `tfplan`. The contract is to block files
    that are tracked in source control (or staged for the next
    commit); files matched only by `.gitignore` are intentionally
    allowed. We delegate the source-controlled detection to
    `git ls-files`, which already considers both tracked + staged
    entries and is the canonical source for "what is in version
    control."

    A test that runs against a synthetic tmpdir (no `.git` present)
    falls back to the filesystem walk so the unit tests can build
    pseudo-trees without initializing a git repo. The fallback only
    triggers when there is no usable git index, never in real-repo
    use.
    """
    tracked = _git_tracked_under_roots(repo_root)
    if tracked is None:
        # No git index — synthetic test mode. Walk the filesystem.
        return _walk_filesystem_artifacts(repo_root)
    return [p for p in tracked if _generated_artifact_match(p)]


def _walk_filesystem_artifacts(repo_root: Path) -> list[str]:
    """Test-mode fallback: walk `_GENERATED_ARTIFACT_ROOTS` on disk
    and return matching repo-relative paths. Production code always
    reaches `_git_tracked_under_roots`; this branch is only exercised
    by unit tests building a synthetic tmpdir tree without a `.git`
    directory."""
    candidates: list[str] = []
    for root in _GENERATED_ARTIFACT_ROOTS:
        base = repo_root / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = _repo_relative(path, repo_root)
            if _generated_artifact_match(rel):
                candidates.append(rel)
    return candidates


def _git_tracked_under_roots(repo_root: Path) -> list[str] | None:
    """Return all tracked (and staged) repo-relative paths under
    `_GENERATED_ARTIFACT_ROOTS`, or `None` if `repo_root` is not a
    git working tree."""
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
        *_GENERATED_ARTIFACT_ROOTS,
    ]
    try:
        # `--cached` enumerates tracked files; `--others
        # --exclude-standard` adds untracked files NOT ignored by
        # gitignore — that captures `git add -f` candidates that
        # bypassed .gitignore and would otherwise be invisible to a
        # tracked-only check until they hit the index.
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.decode("utf-8", errors="replace")
    return [entry for entry in output.split("\0") if entry]


# Centralized scope for the `no-populated-secret-env-files` check
# (ADR-004-R9). Each entry is a repo-relative path prefix under which
# `*-secrets.env` files are scanned. Adding a future overlay (e.g.
# `platform/k8s/gcp/overlays/gcp-prod/`) is automatically covered;
# adding a new top-level location (e.g. a different cluster tree) is
# one entry here.
_SECRET_ENV_ROOTS: tuple[str, ...] = ("platform/k8s/",)

# Basename suffix that selects "secret env" files. Matched on the
# basename only so unrelated `*.env` files (config-bearing, not
# secret-bearing) are not scanned by this check.
_SECRET_ENV_SUFFIX = "-secrets.env"

# Fail-loud synthetic values that may appear as the RHS of an
# assignment in a tracked secret env file. The intent is that
# committed files render Kustomize / kube-linter / kubeconform
# successfully while making it obvious to anyone who deploys with the
# committed values that they have NOT supplied real secrets. Real
# values flow in at deploy time from GitHub Secrets, GCP Secret
# Manager, a gitignored local env file, or a deploy-time Kubernetes
# Secret.
#
# The allowlist is intentionally small and FIXED. Codex review cycle 3
# caught that an earlier `<...>` regex would accept any angle-bracket
# value (e.g. `DB_PASSWORD=<attacker-known-password>`) — a committer
# could wrap a real low-entropy credential in brackets, the guard
# would call it a placeholder, and Kustomize would treat the bracketed
# bytes as the literal Secret value at deploy. The bracket-syntax
# entries below are therefore an explicit fixed set, not a pattern.
# Broader synonyms must come through a deliberate ADR update, not
# ad-hoc growth.
_SECRET_ENV_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        # Bare placeholder tokens
        "REPLACE_AT_DEPLOY",
        "CHANGE_ME",
        "PLACEHOLDER",
        "EXAMPLE",
        # Equivalent bracketed forms (conventional in some example files);
        # the bracket allowlist is fixed, not pattern-based, to close the
        # angle-bracket bypass from cycle 3.
        "<replace-at-deploy>",
        "<replace_at_deploy>",
        "<REPLACE_AT_DEPLOY>",
        "<change-me>",
        "<change_me>",
        "<CHANGE_ME>",
        "<placeholder>",
        "<PLACEHOLDER>",
        "<example>",
        "<EXAMPLE>",
    }
)

def _is_secret_env_in_scope(rel_path: str) -> bool:
    """Return True for a repo-relative path that the secret-env check scans."""
    if not any(rel_path.startswith(root) for root in _SECRET_ENV_ROOTS):
        return False
    basename = rel_path.rsplit("/", 1)[-1]
    return basename.endswith(_SECRET_ENV_SUFFIX)


def _iter_secret_env_candidates(repo_root: Path) -> list[str]:
    """Return repo-relative paths of secret-env files in scope.

    Mirrors the tracked-only contract of
    `check_no_tracked_generated_artifacts`: prefer `git ls-files` so
    gitignored local-dev files (e.g. a developer's
    `platform-runtime-secrets.local.env`) are intentionally NOT
    scanned. Falls back to a filesystem walk only in the synthetic
    tmpdir test path where no `.git` directory exists.
    """
    tracked = _git_tracked_under_roots_for_secret_env(repo_root)
    if tracked is None:
        return _walk_filesystem_secret_env(repo_root)
    return [p for p in tracked if _is_secret_env_in_scope(p)]


def _git_tracked_under_roots_for_secret_env(repo_root: Path) -> list[str] | None:
    """Tracked + non-ignored repo-relative paths under the secret-env
    roots, or `None` if `repo_root` is not a git working tree."""
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
        *_SECRET_ENV_ROOTS,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # `git` unavailable or hung — fall back to filesystem walk so
        # the check still runs (synthetic-mode contract).
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.split(b"\x00")
    return [entry.decode("utf-8") for entry in raw if entry]


def _walk_filesystem_secret_env(repo_root: Path) -> list[str]:
    """Test-mode fallback: walk the configured secret-env roots."""
    candidates: list[str] = []
    for root in _SECRET_ENV_ROOTS:
        base = repo_root / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = _repo_relative(path, repo_root)
            if _is_secret_env_in_scope(rel):
                candidates.append(rel)
    return candidates


def _is_synthetic_placeholder(value: str) -> bool:
    """Return True if `value` is an allowed fail-loud placeholder.

    The allowlist is a fixed set (see `_SECRET_ENV_PLACEHOLDERS`).
    Pattern-based bracket matching was removed in cycle 3 because it
    accepted arbitrary `<...>` content, which would allow a committer
    to hide a real credential as `<attacker-known-password>` and have
    the guardrail pass.
    """
    stripped = value.strip()
    if stripped == "":
        return True
    return stripped in _SECRET_ENV_PLACEHOLDERS


def _scan_secret_env_file(abs_path: Path, rel_path: str) -> list[Violation]:
    """Return violations for any populated, non-placeholder line.

    Parsing rules:

    - A line whose first non-whitespace character is `#`, or that is
      blank after strip, is a comment / blank and is skipped.
    - Any other line MUST contain `=` and is parsed by splitting on
      the first `=`. The LHS is treated as the variable name verbatim
      (any shape — `KEY`, `db.password`, `api-token`, `export KEY`)
      so non-identifier-key shapes cannot bypass the value check.
    - Inline `# ...` is NOT a comment. Kustomize's
      `secretGenerator.envs` loader follows the Docker env_file
      format: `#` is a comment only when it is the first non-
      whitespace character on a line; mid-line `#` is part of the
      value. Treating mid-line `#` as a comment would create a
      bypass (`TOKEN=#real-secret` would normalize to empty and pass
      the placeholder check while the bytes remain in source).
    - A non-comment, non-blank line that does NOT contain `=` is
      flagged as malformed so a committed value smuggled in via a
      non-`=` shape (free text, YAML, etc.) cannot slip past the
      value check.

    The violation message names the line number, the variable shape,
    and the path; it never echoes the rejected value, per the
    preflight contract that validation reports paths and variable
    names only.
    """
    violations: list[Violation] = []
    try:
        text = abs_path.read_text(encoding="utf-8")
    except OSError:
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        if "=" not in stripped_line:
            violations.append(
                Violation(
                    check="no-populated-secret-env-files",
                    rule_id="ADR-004-R9",
                    path=rel_path,
                    message=(
                        f"Tracked secret-env line {lineno} is not a "
                        "comment, blank line, or `KEY=value` "
                        "assignment. Use one of the allowed synthetic "
                        "placeholders (REPLACE_AT_DEPLOY, CHANGE_ME, "
                        "PLACEHOLDER, EXAMPLE, or <placeholder>) or "
                        "remove the line."
                    ),
                )
            )
            continue
        key, _, rhs = line.partition("=")
        var_name = key.strip()
        if _is_synthetic_placeholder(rhs):
            continue
        violations.append(
            Violation(
                check="no-populated-secret-env-files",
                rule_id="ADR-004-R9",
                path=rel_path,
                message=(
                    f"Tracked secret-env assignment `{var_name}` "
                    f"(line {lineno}) has a non-placeholder value. "
                    "Replace with an allowed synthetic placeholder "
                    "(REPLACE_AT_DEPLOY, CHANGE_ME, PLACEHOLDER, "
                    "EXAMPLE, or <placeholder>); real values must come "
                    "from GCP Secret Manager, a gitignored local env "
                    "file, or a deploy-time Kubernetes Secret."
                ),
            )
        )
    return violations


def check_no_populated_secret_env_files(
    repo_root: Path, files: list[str] | None
) -> list[Violation]:
    """Forbid populated assignments in tracked `*-secrets.env` files (ADR-004-R9).

    Scans tracked `*-secrets.env` files under `_SECRET_ENV_ROOTS` (currently
    `platform/k8s/`). Allows comments, blank lines, empty assignments
    (`KEY=`), and a small synthetic-placeholder set. Anything else is
    flagged as a real value that must not ship in source.

    Reports violations with `rule_id="ADR-004-R9"`. Violation messages
    name the path and the variable name; they NEVER echo the rejected
    value. Mirrors `check_no_tracked_generated_artifacts` in using
    `git ls-files` for containment (so gitignored local-dev files are
    intentionally not scanned) with a filesystem-walk fallback for
    synthetic-tmpdir unit tests.
    """
    if files is not None:
        in_scope = sorted({p for p in files if _is_secret_env_in_scope(p)})
    else:
        in_scope = sorted(set(_iter_secret_env_candidates(repo_root)))
    violations: list[Violation] = []
    for rel in in_scope:
        abs_path = repo_root / rel
        if not abs_path.exists() or not abs_path.is_file():
            continue
        violations.extend(_scan_secret_env_file(abs_path, rel))
    return violations


def check_no_tracked_generated_artifacts(
    repo_root: Path, files: list[str] | None
) -> list[Violation]:
    """Forbid tracked generated/sensitive artifacts (ADR-004-R8).

    Three artifact families are blocked, each scoped narrowly:

    - Terraform plan outputs (`tfplan`, `plan.out`, `*.tfplan`,
      `*.tfplan.binary`) under `platform/terraform/environments/` and
      `platform/terraform/gcp/environments/`. Plan files are generated
      security-sensitive artifacts: they may carry state-derived
      values, resource addresses, provider metadata, and deployment-
      specific operational details.
    - License / authcode bootstrap material (`authcodes`,
      `*.authcodes`) under `temp/bootstrap/`. These pre-staging
      outputs must not be tracked.
    - Polaris range build output under `scenario-dev/polaris/build/`.
      This is generated/runtime material, not source.

    The check fails closed at the staged-source boundary. It does NOT
    parse plan binaries or echo file content — the violation message
    names the repo-relative path and the remediation.
    """
    violations: list[Violation] = []
    if files is not None:
        in_scope = sorted({p for p in files if _generated_artifact_match(p)})
    else:
        in_scope = sorted(set(_iter_artifact_candidates(repo_root)))
    for rel in in_scope:
        violations.append(
            Violation(
                check="no-tracked-generated-artifacts",
                rule_id="ADR-004-R8",
                path=rel,
                message=(
                    "Generated/sensitive artifact must not be tracked in source. "
                    "Remove with `git rm` and ensure the path is covered by "
                    ".gitignore + the ADR-004-R8 guardrail."
                ),
            )
        )
    return violations
