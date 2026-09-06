#!/usr/bin/env python3
"""Lint the GCP GitHub-Actions Workload Identity Federation trust (ADR-004-R23).

Credentialed GCP CI must establish reviewed-code provenance at the WIF trust
boundary, not inside dispatched workflow code (#1690). This guard pins the
`cicd-oidc-identity` module to the exact-subject federation shape:

- the Workload Identity **provider** `attribute_condition` must gate on an exact
  protected `assertion.ref` (not repository-only), so a feature-branch or tag
  dispatch is denied at the pool even when its `environment:` `sub` matches;
- service-account WIF bindings (`roles/iam.workloadIdentityUser`) must name exact
  `principal://.../subject/<sub>` members, never a repository-wide
  `principalSet://.../attribute.repository/...` member; and
- build, validate, promote, deploy, and destroy subject sets must be pairwise
  disjoint, correctly wired to their own SAs, and keep narrow build/validate/
  promote role classes; and
- the `CKV_GCP_125` repository-scope Checkov waiver must not survive, since the
  exact `assertion.ref`/`assertion.sub` pin satisfies it.

The check no-ops on Terraform that does not define these resources, so unrelated
modules and fixtures are unaffected. It mirrors the AWS
`check_tf_iam_role_naming` guard's comment-stripping discipline (no keying on
resource labels or prose).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

WIF_MODULE_GLOBS: tuple[str, ...] = (
    "platform/terraform/gcp/modules/cicd-oidc-identity/*.tf",
)

PROVIDER_RE = re.compile(
    r'^\s*resource\s+"google_iam_workload_identity_pool_provider"\s+"([^"]+)"\s*\{'
)
SA_MEMBER_RE = re.compile(
    r'^\s*resource\s+"google_service_account_iam_member"\s+"([^"]+)"\s*\{'
)
WORKLOAD_IDENTITY_USER = "roles/iam.workloadIdentityUser"
# A repository-wide principalSet trusts every workflow/ref/actor in the repo; the
# exact-subject principal is the real impersonation boundary (ADR-004-R23).
FORBIDDEN_PRINCIPALSET = "principalSet://"
REPO_ATTRIBUTE = "attribute.repository/"
EXACT_SUBJECT_MARKER = "/subject/"
# The repository-scope waiver, matched as the Checkov skip directive (not the
# bare rule id) so prose naming the rule does not false-positive.
CKV_GCP_125_SKIP_RE = re.compile(r"checkov:skip\s*=\s*CKV_GCP_125")
# The static condition and the SA-binding list must not drift: the condition is
# written out literally (Checkov cannot render join()), so the guard compares the
# `assertion.sub == '<sub>'` clauses against the local.federated_subjects list.
SUBJECT_EQ_RE = re.compile(r"assertion\.sub\s*==\s*'([^']+)'")
# The ref gate may be inlined in the condition or factored into a `ref_condition`
# local (ADR-037-R7). Match the equality FORM, not the bare token, so the
# attribute_mapping (`"attribute.ref" = "assertion.ref"`) cannot false-pass it.
REF_EQ_RE = re.compile(r"assertion\.ref\s*==")
GCP_DEV_REF_EQ_RE = re.compile(r"assertion\.ref\s*==\s*'refs/heads/gcp-dev'")
GCP_DEV_SUBJECT_REF_PAIR_RE = re.compile(
    r"assertion\.ref\s*==\s*'refs/heads/gcp-dev'\s*&&\s*"
    r"assertion\.sub\s*==\s*'[^']*:environment:gcp-dev'"
)
DOUBLE_QUOTED_RE = re.compile(r'"([^"]+)"')
# The invariant checks scope to the attribute_condition VALUE, not the whole
# provider block: attribute_mapping maps assertion.sub/ref/repository regardless
# of the condition, so a block-wide token scan would pass even a repository-only
# condition (codex #1690 review). CEL literals use single quotes, so the HCL
# double-quoted value contains no inner `"` and `[^"]*` captures it whole.
ATTRIBUTE_CONDITION_ASSIGNMENT_RE = re.compile(r"attribute_condition\s*=")
_PROFILE_ARMS_PATTERN = (
    r'var\.environment\s*==\s*"gcp-dev"\s*\?\s*"(?P<gcp_dev>[^"]*)"\s*:\s*'
    r'var\.environment\s*==\s*"proof"\s*\?\s*"(?P<proof>[^"]*)"\s*:\s*'
    r'"(?P<prod>[^"]*)"'
)
PROFILED_TEMPLATE_CONDITION_RE = re.compile(
    r'attribute_condition\s*=\s*"(?P<common>.*?)\$\{\s*'
    + _PROFILE_ARMS_PATTERN
    + r'\s*\}\s*"',
    re.DOTALL,
)
PROFILED_DIRECT_CONDITION_RE = re.compile(
    r"attribute_condition\s*=\s*" + _PROFILE_ARMS_PATTERN,
    re.DOTALL,
)
STATIC_CONDITION_RE = re.compile(r'attribute_condition\s*=\s*"([^"]*)"')
PURPOSES: tuple[str, ...] = ("build", "validate", "promote", "deploy", "destroy")
PURPOSE_SUBJECTS_HEADER_RE = re.compile(r"^\s*purpose_subjects\s*=\s*\{")
VARIABLE_HEADER_RE = re.compile(r'^\s*variable\s+"([^"]+)"\s*\{')
OUTPUT_RE = re.compile(r'^\s*output\s+"([^"]+)"\s*\{', re.MULTILINE)
REQUIRED_OUTPUTS: frozenset[str] = frozenset(
    {
        "workload_identity_provider",
        "packer_build_service_account_email",
        "packer_validate_service_account_email",
        "packer_promote_service_account_email",
        "deploy_service_account_email",
        "destroy_service_account_email",
    }
)


@dataclass
class Violation:
    file: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.reason}"


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def _extract_resource_block(lines: list[str], start_idx: int) -> list[str]:
    depth = _brace_delta(lines[start_idx])
    idx = start_idx + 1
    while idx < len(lines) and depth > 0:
        depth += _brace_delta(lines[idx])
        idx += 1
    return lines[start_idx:idx]


def _strip_hcl_comments(text: str) -> str:
    """Drop `#` line comments so trust matching never keys on prose.

    `#` never appears inside the provider condition, principal members, or
    subjects the checks inspect, so this is a safe, precise strip. `//` / `/* */`
    stripping is intentionally avoided (a `principalSet://` member contains a
    literal `//`).
    """
    return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())


def _attribute_condition_alternatives(block: str) -> dict[str, str]:
    """Return each CEL string the supported profile selector can emit."""
    profiled = PROFILED_TEMPLATE_CONDITION_RE.search(block)
    if profiled:
        common = profiled.group("common")
        return {
            profile: common + profiled.group(profile)
            for profile in ("gcp_dev", "proof", "prod")
        }
    profiled = PROFILED_DIRECT_CONDITION_RE.search(block)
    if profiled:
        return {
            profile: profiled.group(profile)
            for profile in ("gcp_dev", "proof", "prod")
        }
    static = STATIC_CONDITION_RE.search(block)
    return {"static": static.group(1)} if static else {}


def _iter_resource_blocks(
    lines: list[str], header_re: re.Pattern[str]
) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    idx = 0
    while idx < len(lines):
        if header_re.match(lines[idx]):
            block = _extract_resource_block(lines, idx)
            blocks.append((idx + 1, block))
            idx += len(block)
            continue
        idx += 1
    return blocks


def check_provider_condition(
    path: Path, lines: list[str], text: str
) -> list[Violation]:
    """WIF provider must pin an exact protected assertion.ref, not repo-only."""
    violations: list[Violation] = []
    # The ref gate may be factored into a `ref_condition` local (ADR-037-R7), so
    # its `assertion.ref ==` equality can live outside the provider block.
    file_has_ref_gate = bool(REF_EQ_RE.search(_strip_hcl_comments(text)))
    for line_no, block in _iter_resource_blocks(lines, PROVIDER_RE):
        raw = "\n".join(block)
        stripped = _strip_hcl_comments(raw)
        # The repository-scope Checkov waiver lives in a `#` comment, so scan the
        # RAW block text (before comment stripping) to catch a surviving skip.
        # Match the skip DIRECTIVE precisely so explanatory prose that names the
        # rule (e.g. "replaces the CKV_GCP_125 waiver") does not false-positive.
        if CKV_GCP_125_SKIP_RE.search(raw):
            violations.append(
                Violation(
                    path,
                    line_no,
                    "WIF provider must not retain the CKV_GCP_125 repository-scope "
                    "waiver once assertion.ref/sub are pinned (ADR-004-R23, #1690)",
                )
            )
        # Scope every invariant to the attribute_condition VALUE, not the whole
        # block: attribute_mapping maps assertion.sub/ref/repository regardless of
        # the condition (codex #1690 review). No static condition -> unguarded.
        condition_match = ATTRIBUTE_CONDITION_ASSIGNMENT_RE.search(stripped)
        alternatives = _attribute_condition_alternatives(stripped)
        if condition_match is None or not alternatives:
            violations.append(
                Violation(
                    path,
                    line_no,
                    "WIF provider must define static attribute_condition strings "
                    "(ADR-004-R23, #1690)",
                )
            )
            continue
        # A secure dev arm must not mask a weakened proof/prod arm. Validate
        # every emitted CEL alternative independently, including its exact
        # profile subject inventory.
        expected_contexts = {
            "gcp_dev": {"gcp-build-dev", "gcp-validate-dev", "gcp-dev", "gcp-dev-destroy"},
            "proof": {"gcp-build-proof", "gcp-validate-proof"},
            "prod": {"gcp-promote-prod"},
        }
        for profile, condition in alternatives.items():
            missing_invariants: list[str] = []
            if "assertion.repository" not in condition:
                missing_invariants.append("assertion.repository")
            if not (
                ("assertion.ref" in condition or "ref_condition" in condition)
                and file_has_ref_gate
            ):
                missing_invariants.append("exact protected assertion.ref")
            if SUBJECT_EQ_RE.search(condition) is None:
                missing_invariants.append("literal assertion.sub")
            if missing_invariants:
                violations.append(
                    Violation(
                        path,
                        line_no,
                        "WIF provider must enforce "
                        f"{', '.join(missing_invariants)} in every profile arm; {profile} is incomplete "
                        "(ADR-004-R23, ADR-037-R7, #1699)",
                    )
                )
                continue
            if profile in expected_contexts:
                contexts = {
                    subject.rsplit(":environment:", 1)[1]
                    for subject in SUBJECT_EQ_RE.findall(condition)
                    if ":environment:" in subject
                }
                if contexts != expected_contexts[profile]:
                    violations.append(
                        Violation(
                            path,
                            line_no,
                            f"WIF {profile} profile arm has the wrong exact Environment subjects: {sorted(contexts)} (#1699)",
                        )
                    )
            gcp_dev_refs = GCP_DEV_REF_EQ_RE.findall(condition)
            if gcp_dev_refs and (
                len(gcp_dev_refs) != 1
                or len(GCP_DEV_SUBJECT_REF_PAIR_RE.findall(condition)) != 1
            ):
                violations.append(
                    Violation(
                        path,
                        line_no,
                        "refs/heads/gcp-dev must be admitted exactly once and paired "
                        "directly with the exact gcp-dev Environment subject "
                        "(ADR-004-R23)",
                    )
                )
    return violations


def check_sa_wif_members(path: Path, lines: list[str], text: str) -> list[Violation]:
    """WIF service-account bindings must use exact subject principals."""
    violations: list[Violation] = []
    wif_blocks = [
        (line_no, block)
        for line_no, block in _iter_resource_blocks(lines, SA_MEMBER_RE)
        if WORKLOAD_IDENTITY_USER in "\n".join(block)
    ]
    if not wif_blocks:
        return violations

    # A member may reference a `local.*` value, so a repository-wide principalSet
    # can hide in the module `locals` block. Scan the whole comment-stripped file
    # for the forbidden repo-wide member, then require an exact-subject member.
    file_compact = re.sub(r"\s+", "", _strip_hcl_comments(text))
    if FORBIDDEN_PRINCIPALSET in file_compact and REPO_ATTRIBUTE in file_compact:
        line_no = wif_blocks[0][0]
        violations.append(
            Violation(
                path,
                line_no,
                "WIF service-account binding must use exact "
                "principal://.../subject/<sub> members, never a repository-wide "
                "principalSet://.../attribute.repository/... member "
                "(ADR-004-R23, #1690)",
            )
        )
    if EXACT_SUBJECT_MARKER not in file_compact:
        line_no = wif_blocks[0][0]
        violations.append(
            Violation(
                path,
                line_no,
                "WIF service-account binding must name at least one exact "
                "principal://.../subject/<sub> member (ADR-004-R23, #1690)",
            )
        )
    return violations


def check_subject_consistency(path: Path, text: str) -> list[Violation]:
    """Static condition subjects must equal the purpose-specific subject map.

    The provider condition is written out literally (Checkov cannot render
    ``join()``), so this guard fails the build if the condition's
    ``assertion.sub == '<sub>'`` clauses and the single-source
    ``local.federated_subjects`` list diverge - which would silently strand or
    over-trust a caller. No-op unless both are present.
    """
    stripped = _strip_hcl_comments(text)
    lines = stripped.splitlines()
    purpose_blocks = _iter_resource_blocks(lines, PURPOSE_SUBJECTS_HEADER_RE)
    condition_subs = {_normalize_image_subject(s) for s in SUBJECT_EQ_RE.findall(stripped)}
    if not purpose_blocks or not condition_subs:
        return []
    purpose_subs = {
        _normalize_image_subject(value)
        for value in DOUBLE_QUOTED_RE.findall("\n".join(purpose_blocks[0][1]))
        if ":environment:" in value
    }
    if purpose_subs == condition_subs:
        return []
    missing_from_condition = purpose_subs - condition_subs
    extra_in_condition = condition_subs - purpose_subs
    detail = []
    if missing_from_condition:
        detail.append(
            f"missing from attribute_condition: {sorted(missing_from_condition)}"
        )
    if extra_in_condition:
        detail.append(f"not in local.federated_subjects: {sorted(extra_in_condition)}")
    return [
        Violation(
            path,
            1,
            "WIF attribute_condition subjects must equal local.purpose_subjects "
            f"({'; '.join(detail)}) (ADR-004-R23, #1690)",
        )
    ]


def _normalize_image_subject(subject: str) -> str:
    """Collapse concrete dev/proof image Environments to the module's profile seam."""
    return re.sub(
        r"environment:gcp-(build|validate)-(?:dev|proof)$",
        r"environment:gcp-\1-${local.image_environment}",
        subject,
    )


def _purpose_entries(block: str) -> dict[str, set[str]]:
    entries: dict[str, set[str]] = {}
    for index, purpose in enumerate(PURPOSES):
        next_names = PURPOSES[index + 1 :]
        stop = "|".join(re.escape(name) for name in next_names)
        tail = rf"(?=^\s*(?:{stop})\s*=|^\s*\}})" if stop else r"(?=^\s*\})"
        match = re.search(rf"^\s*{purpose}\s*=\s*(.*?){tail}", block, re.MULTILINE | re.DOTALL)
        if match:
            entries[purpose] = {
                _normalize_image_subject(value)
                for value in DOUBLE_QUOTED_RE.findall(match.group(1))
                if ":environment:" in value
            }
    return entries


def check_purpose_isolation(path: Path, lines: list[str], text: str) -> list[Violation]:
    """Require five disjoint subject sets and correctly wired SA bindings."""
    violations: list[Violation] = []
    stripped = _strip_hcl_comments(text)
    stripped_lines = stripped.splitlines()
    blocks = _iter_resource_blocks(stripped_lines, PURPOSE_SUBJECTS_HEADER_RE)
    if not blocks:
        # Legacy shared-identity module shape is invalid; unrelated modules no-op.
        if re.search(
            r'resource\s+"google_service_account"\s+"packer_build"\s*\{',
            stripped,
        ):
            violations.append(
                Violation(path, 1, "GCP CI trust must define five purpose-specific subject sets (ADR-004-R23, #1699)")
            )
        return violations

    line_no, block_lines = blocks[0]
    entries = _purpose_entries("\n".join(block_lines))
    missing = set(PURPOSES) - set(entries)
    if missing:
        violations.append(Violation(path, line_no, f"purpose subject map is missing {sorted(missing)} (#1699)"))
    seen: dict[str, str] = {}
    for purpose, subjects in entries.items():
        for subject in subjects:
            if prior := seen.get(subject):
                violations.append(
                    Violation(path, line_no, f"purpose subject sets must be pairwise disjoint; {prior} and {purpose} share {subject} (#1699)")
                )
            seen[subject] = purpose

    for purpose in PURPOSES:
        sa_name = "packer_build" if purpose == "build" else purpose
        binding_name = "packer_build_wif" if purpose == "build" else f"{purpose}_wif"
        pattern = re.compile(
            rf'resource\s+"google_service_account_iam_member"\s+"{binding_name}"\s*\{{(.*?)\n\}}',
            re.DOTALL,
        )
        match = pattern.search(stripped)
        expected_subjects = f"local.purpose_subject_principals.{purpose}"
        expected_sa = f"google_service_account.{sa_name}"
        if not match or expected_subjects not in match.group(1) or expected_sa not in match.group(1):
            violations.append(
                Violation(path, 1, f"{purpose} WIF binding must use only its purpose principals and service account (#1699)")
            )
    return violations


def _variable_values(lines: list[str], variable_name: str) -> set[str] | None:
    for _, block in _iter_resource_blocks(lines, VARIABLE_HEADER_RE):
        header = block[0]
        match = VARIABLE_HEADER_RE.match(header)
        if match and match.group(1) == variable_name:
            return set(DOUBLE_QUOTED_RE.findall("\n".join(block)))
    return None


def check_role_boundaries(path: Path, lines: list[str]) -> list[Violation]:
    """Reject role/permission classes forbidden to narrow CI identities."""
    violations: list[Violation] = []
    stripped_lines = _strip_hcl_comments("\n".join(lines)).splitlines()
    validate_roles = _variable_values(stripped_lines, "validate_roles")
    if validate_roles is not None:
        forbidden = {
            "roles/compute.admin",
            "roles/storage.admin",
            "roles/cloudbuild.builds.editor",
            "roles/iam.serviceAccountAdmin",
            "roles/resourcemanager.projectIamAdmin",
        }
        if overlap := validate_roles & forbidden:
            violations.append(Violation(path, 1, f"validate role set contains forbidden broad roles {sorted(overlap)} (#1699)"))

    validate_permissions = _variable_values(stripped_lines, "validate_permissions")
    if validate_permissions is not None:
        forbidden = {"compute.images.create", "compute.images.delete", "compute.images.deprecate"}
        if overlap := validate_permissions & forbidden:
            violations.append(Violation(path, 1, f"validate permission set crosses image-build/promotion authority {sorted(overlap)} (#1699)"))

    promote_permissions = _variable_values(stripped_lines, "promote_permissions")
    if promote_permissions is not None:
        forbidden_prefixes = ("compute.instances.", "storage.", "cloudbuild.", "iam.")
        overlap = sorted(value for value in promote_permissions if value.startswith(forbidden_prefixes))
        if overlap:
            violations.append(Violation(path, 1, f"promote permission set crosses instance/storage/build/IAM authority {overlap} (#1699)"))

    build_roles = _variable_values(stripped_lines, "build_roles")
    if build_roles is not None and "roles/storage.admin" in build_roles:
        violations.append(Violation(path, 1, "build role set must use resource-scoped GCS grants, not roles/storage.admin (#1699)"))
    return violations


def check_output_contract(path: Path, text: str) -> list[Violation]:
    """Purpose identity module must expose every explicit secret value."""
    if path.name != "outputs.tf" or path.parent.name != "cicd-oidc-identity":
        return []
    outputs = set(OUTPUT_RE.findall(_strip_hcl_comments(text)))
    missing = REQUIRED_OUTPUTS - outputs
    if not missing:
        return []
    return [
        Violation(
            path,
            1,
            f"GCP CI identity module must publish explicit purpose outputs; missing {sorted(missing)} (#1699)",
        )
    ]


def check_file(path: Path) -> list[Violation]:
    if path.suffix != ".tf":
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    violations = check_provider_condition(path, lines, text)
    violations.extend(check_sa_wif_members(path, lines, text))
    violations.extend(check_subject_consistency(path, text))
    violations.extend(check_purpose_isolation(path, lines, text))
    violations.extend(check_role_boundaries(path, lines))
    violations.extend(check_output_contract(path, text))
    return violations


def iter_target_files(repo_root: Path, argv: list[str]) -> list[Path]:
    if argv:
        return [Path(arg).resolve() for arg in argv]
    files: list[Path] = []
    for pattern in WIF_MODULE_GLOBS:
        files.extend(sorted(repo_root.glob(pattern)))
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[Violation] = []
    for path in iter_target_files(repo_root, args):
        if not path.is_file():
            continue
        violations.extend(check_file(path))
    if violations:
        for violation in violations:
            print(violation)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
