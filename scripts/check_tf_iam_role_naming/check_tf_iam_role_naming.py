#!/usr/bin/env python3
"""Lint IAM role and instance profile naming in Terraform modules.

Deploy-managed IAM resources must use the iam_name_prefix seam so role names
land in the shifter-* namespace without renaming unrelated infrastructure.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

IAM_MODULE_GLOBS: tuple[str, ...] = (
    "platform/terraform/modules/portal/ec2/*.tf",
    "platform/terraform/modules/portal/vpc/*.tf",
    "platform/terraform/modules/portal/cognito/*.tf",
    "platform/terraform/modules/portal/rds/*.tf",
    "platform/terraform/modules/portal/ctfd/*.tf",
    "platform/terraform/modules/range/vpc/*.tf",
    "platform/terraform/modules/engine-provisioner/*.tf",
    "platform/terraform/modules/guacamole/*.tf",
    "platform/terraform/modules/log-aggregation/*.tf",
)

IAM_RESOURCE_RE = re.compile(
    r'^\s*resource\s+"(aws_iam_role|aws_iam_instance_profile)"\s+"([^"]+)"\s*\{'
)
NAME_ATTR_RE = re.compile(r'^\s*name\s*=\s*(.+)$')
LEGACY_PREFIX_RE = re.compile(r"\$\{var\.name_prefix\}")
IAM_PREFIX_OK_RE = re.compile(r"iam_name_prefix")
LEGACY_OIDC_PATTERN_RE = re.compile(
    r"role/(dev-portal-\*|prod-portal-\*|dev-range-\*|prod-range-\*)"
)
ATTACH_ALLOWLIST_POLICIES: tuple[str, ...] = (
    "AmazonSSMManagedInstanceCore",
    "AmazonECSTaskExecutionRolePolicy",
    "AWSLambdaBasicExecutionRole",
    "AmazonRDSEnhancedMonitoringRole",
)
ATTACHMENT_RESOURCE_RE = re.compile(
    r'^\s*resource\s+"aws_iam_role_policy_attachment"\s+"([^"]+)"\s*\{'
)
# AWS caps a role at 10 managed-policy attachments. Consolidating the OIDC
# deploy role by AWS category (#254) keeps it well under that hard limit with
# headroom for future domains. The cap fails the build before the role drifts
# back toward the limit; adding a service should extend an existing category
# policy, not a new attachment.
OIDC_ATTACHMENT_CAP = 6

IAM_POLICY_RESOURCE_RE = re.compile(r'^\s*resource\s+"aws_iam_policy"\s+"([^"]+)"\s*\{')
# AWS caps a customer-managed policy document at 6,144 characters (whitespace
# excluded). The OIDC deploy role consolidates many AWS services into 5 category
# policies (#254), so a category can grow toward that ceiling as services are
# added. This guard fails the build before a category exceeds the limit and the
# deploy hits LimitExceeded at apply time. It measures the whitespace-stripped
# HCL of each policy block, which is a conservative over-estimate of the rendered
# JSON because the `${...account_id}`/`${...aws_region}` interpolations are
# longer than the values they render to.
OIDC_POLICY_DOC_LIMIT = 6144

# Base-image-pipeline role (#1656). The packer.yml base `build` job assumes a
# dedicated least-privilege role instead of the broad deploy role. Its OIDC trust
# MUST pin the exact protected-branch subjects (never repo:...:*), and its inline
# policy MUST scope iam:PassRole to the EXACT env range role passed to EC2 - never
# shifter-*, *-range-instance, or any other wildcard - so the fresh-boot verifier
# cannot pass a more-privileged profile (ADR-004-R22). These checks run only when
# the role / inline policy is present, so deploy-only github-oidc.tf fixtures are
# unaffected.
IMAGE_ROLE_RE = re.compile(
    r'^\s*resource\s+"aws_iam_role"\s+"github_actions_image"\s*\{'
)
IMAGE_POLICY_RE = re.compile(
    r'^\s*resource\s+"aws_iam_role_policy"\s+"image_pipeline"\s*\{'
)
IMAGE_TRUST_REQUIRED_SUBS: tuple[str, ...] = (
    "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/dev",
    "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main",
)
IMAGE_TRUST_WILDCARD_SUB = "repo:${var.github_org}/${var.github_repo}:*"
IMAGE_PASSROLE_EXACT_RESOURCE = (
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:"
    "role/shifter-${var.environment}-range-range-instance"
)
IMAGE_PASSROLE_FORBIDDEN_WILDCARDS: tuple[str, ...] = (
    "role/shifter-*",
    "role/${var.environment}-*",
    "role/*",
    "*-range-instance",
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


def _extract_resource_block(lines: list[str], start_idx: int) -> tuple[int, list[str]]:
    depth = _brace_delta(lines[start_idx])
    idx = start_idx + 1
    while idx < len(lines) and depth > 0:
        depth += _brace_delta(lines[idx])
        idx += 1
    return idx, lines[start_idx:idx]


def check_iam_resource_names(path: Path, lines: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    idx = 0
    while idx < len(lines):
        match = IAM_RESOURCE_RE.match(lines[idx])
        if not match:
            idx += 1
            continue

        _, block_lines = _extract_resource_block(lines, idx)
        for offset, line in enumerate(block_lines):
            name_match = NAME_ATTR_RE.match(line)
            if not name_match:
                continue
            value = name_match.group(1)
            line_no = idx + offset + 1
            if LEGACY_PREFIX_RE.search(value) and not IAM_PREFIX_OK_RE.search(value):
                violations.append(
                    Violation(
                        path,
                        line_no,
                        "IAM resource name must use iam_name_prefix/local.iam_name_prefix, not var.name_prefix alone",
                    )
                )
        idx += 1
    return violations


def check_github_oidc_iam_scoped(path: Path, text: str) -> list[Violation]:
    violations: list[Violation] = []
    if LEGACY_OIDC_PATTERN_RE.search(text):
        violations.append(
            Violation(
                path,
                1,
                "github-oidc iam_scoped must not retain legacy dev-portal/prod-portal/dev-range role patterns",
            )
        )
    if "iam:AttachRolePolicy" in text and "iam:PolicyArn" not in text:
        violations.append(
            Violation(
                path,
                1,
                "github-oidc iam_scoped must constrain iam:AttachRolePolicy with iam:PolicyArn allowlist",
            )
        )
    for policy in ATTACH_ALLOWLIST_POLICIES:
        if policy not in text:
            violations.append(
                Violation(
                    path,
                    1,
                    f"github-oidc iam_scoped allowlist must include {policy}",
                )
            )
    if "role/shifter-*" not in text.replace(" ", ""):
        violations.append(
            Violation(
                path,
                1,
                "github-oidc iam_scoped must scope deploy-managed roles to role/shifter-*",
            )
        )
    return violations


def check_github_oidc_attachment_cap(path: Path, lines: list[str]) -> list[Violation]:
    attachment_lines = [
        idx for idx, line in enumerate(lines) if ATTACHMENT_RESOURCE_RE.match(line)
    ]
    if len(attachment_lines) > OIDC_ATTACHMENT_CAP:
        return [
            Violation(
                path,
                attachment_lines[OIDC_ATTACHMENT_CAP] + 1,
                f"github-oidc role must attach at most {OIDC_ATTACHMENT_CAP} "
                f"managed policies (found {len(attachment_lines)}); consolidate by "
                "AWS category to keep headroom under the AWS 10-policy limit (#254)",
            )
        ]
    return []


def check_github_oidc_policy_doc_size(path: Path, lines: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    idx = 0
    while idx < len(lines):
        match = IAM_POLICY_RESOURCE_RE.match(lines[idx])
        if not match:
            idx += 1
            continue
        _, block_lines = _extract_resource_block(lines, idx)
        stripped = re.sub(r"\s+", "", "".join(block_lines))
        if len(stripped) > OIDC_POLICY_DOC_LIMIT:
            violations.append(
                Violation(
                    path,
                    idx + 1,
                    f"github-oidc managed policy \"{match.group(1)}\" is "
                    f"{len(stripped)} chars (limit {OIDC_POLICY_DOC_LIMIT}); "
                    "compact or split the category to stay under the AWS "
                    "managed-policy document size limit (#254)",
                )
            )
        idx += 1
    return violations


def _find_named_resource_block(
    lines: list[str], header_re: re.Pattern[str]
) -> list[str] | None:
    idx = 0
    while idx < len(lines):
        if header_re.match(lines[idx]):
            _, block_lines = _extract_resource_block(lines, idx)
            return block_lines
        idx += 1
    return None


def _strip_hcl_comments(text: str) -> str:
    """Drop `#` line comments so guardrail matching never keys on prose.

    github-oidc.tf uses `#` comments (terraform fmt normalizes to them); a
    comment mentioning a forbidden wildcard (e.g. an explanatory
    "not *-range-instance") must not trip the checks below, which match trust /
    policy content only. `#` never appears inside the ARNs, actions, or OIDC
    subjects the checks inspect, so this is a safe, precise strip (`//` / `/* */`
    stripping is intentionally avoided - `instance/*` contains a literal `/*`).
    """
    return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())


def check_github_oidc_image_role_trust(path: Path, lines: list[str]) -> list[Violation]:
    """Base-image role OIDC trust must pin exact protected subjects (#1656).

    No-op when the github_actions_image role is absent, so deploy-only fixtures
    are unaffected; when present, a repo:...:* wildcard sub or a missing exact
    protected-branch subject fails closed.
    """
    block = _find_named_resource_block(lines, IMAGE_ROLE_RE)
    if block is None:
        return []
    compact = re.sub(r"\s+", "", _strip_hcl_comments("\n".join(block)))
    violations: list[Violation] = []
    if IMAGE_TRUST_WILDCARD_SUB.replace(" ", "") in compact:
        violations.append(
            Violation(
                path,
                1,
                "image-pipeline role OIDC trust must pin exact protected-branch "
                "subjects, not a repo:...:* wildcard (ADR-004-R22, #1656)",
            )
        )
    for sub in IMAGE_TRUST_REQUIRED_SUBS:
        if sub.replace(" ", "") not in compact:
            violations.append(
                Violation(
                    path,
                    1,
                    f"image-pipeline role OIDC trust must include exact subject "
                    f"{sub} (ADR-004-R22, #1656)",
                )
            )
    return violations


def check_github_oidc_image_passrole_scope(
    path: Path, lines: list[str]
) -> list[Violation]:
    """Base-image policy iam:PassRole must target the exact range role (#1656).

    No-op when the image_pipeline inline policy is absent; when present, the
    PassRole must name exactly shifter-${var.environment}-range-range-instance,
    carry the ec2.amazonaws.com service condition, and use no wildcard resource.
    """
    block = _find_named_resource_block(lines, IMAGE_POLICY_RE)
    if block is None:
        return []
    text = _strip_hcl_comments("\n".join(block))
    compact = re.sub(r"\s+", "", text)
    violations: list[Violation] = []
    if "iam:PassRole" not in text:
        violations.append(
            Violation(
                path,
                1,
                "image-pipeline policy must grant iam:PassRole scoped to the "
                "exact range role (ADR-004-R22, #1656)",
            )
        )
        return violations
    if IMAGE_PASSROLE_EXACT_RESOURCE.replace(" ", "") not in compact:
        violations.append(
            Violation(
                path,
                1,
                "image-pipeline iam:PassRole must target exactly "
                "role/shifter-${var.environment}-range-range-instance "
                "(ADR-004-R22, #1656)",
            )
        )
    for forbidden in IMAGE_PASSROLE_FORBIDDEN_WILDCARDS:
        if forbidden.replace(" ", "") in compact:
            violations.append(
                Violation(
                    path,
                    1,
                    f"image-pipeline iam:PassRole must not use wildcard resource "
                    f"'{forbidden}' (ADR-004-R22, #1656)",
                )
            )
    if "ec2.amazonaws.com" not in compact:
        violations.append(
            Violation(
                path,
                1,
                "image-pipeline iam:PassRole must be conditioned on "
                "iam:PassedToService = ec2.amazonaws.com (ADR-004-R22, #1656)",
            )
        )
    return violations


def check_file(path: Path) -> list[Violation]:
    if path.suffix != ".tf":
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    violations = check_iam_resource_names(path, lines)
    if path.name == "github-oidc.tf":
        violations.extend(check_github_oidc_iam_scoped(path, text))
        violations.extend(check_github_oidc_attachment_cap(path, lines))
        violations.extend(check_github_oidc_policy_doc_size(path, lines))
        violations.extend(check_github_oidc_image_role_trust(path, lines))
        violations.extend(check_github_oidc_image_passrole_scope(path, lines))
    return violations


def iter_target_files(repo_root: Path, argv: list[str]) -> list[Path]:
    if argv:
        return [Path(arg).resolve() for arg in argv]

    files: list[Path] = []
    for pattern in IAM_MODULE_GLOBS:
        files.extend(sorted(repo_root.glob(pattern)))
    oidc = repo_root / "platform/terraform/global/iam/github-oidc.tf"
    if oidc.exists():
        files.append(oidc)
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
