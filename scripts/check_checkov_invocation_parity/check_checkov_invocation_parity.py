#!/usr/bin/env python3
"""Ensure the blocking CI Checkov Terraform invocation uses canonical policy.

ADR-004-R11 requires one canonical policy at platform/terraform/.checkov.yaml
and blocking (non-soft-fail) execution. Issue #147 requires external modules to
be downloaded so inline module skips apply. Expensive IaC scanning is CI-only;
this guard validates that authoritative invocation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXPECTED_CONFIG = "platform/terraform/.checkov.yaml"
EXPECTED_DIRECTORY = "platform/terraform/"


def _parse_ci_checkov_step(workflow_text: str) -> dict[str, str]:
    """Parse the security-iac Checkov action inputs from _quality.yml."""
    lines = workflow_text.splitlines()
    in_security_iac = False
    in_checkov_step = False
    inputs: dict[str, str] = {}

    for line in lines:
        if re.match(r"^  security-iac:\s*$", line):
            in_security_iac = True
            in_checkov_step = False
            continue
        if in_security_iac and re.match(r"^  [a-z][\w-]*:\s*$", line):
            if not line.strip().startswith("security-iac"):
                in_security_iac = False
                in_checkov_step = False
        if not in_security_iac:
            continue
        if "Checkov IaC Security" in line:
            in_checkov_step = True
            continue
        if (
            in_checkov_step
            and line.strip().startswith("- name:")
            and "Checkov IaC Security" not in line
        ):
            in_checkov_step = False
        if not in_checkov_step:
            continue
        match = re.match(r"\s+(\w+):\s*(.+)\s*$", line)
        if match:
            inputs[match.group(1)] = match.group(2).strip()

    return inputs


def check_repo(repo_root: Path) -> list[str]:
    violations: list[str] = []

    workflow_path = repo_root / ".github" / "workflows" / "_quality.yml"

    if not workflow_path.is_file():
        return ["missing .github/workflows/_quality.yml"]

    ci_inputs = _parse_ci_checkov_step(workflow_path.read_text(encoding="utf-8"))

    if ci_inputs.get("config_file") != EXPECTED_CONFIG:
        violations.append(
            f"CI Checkov config_file must be {EXPECTED_CONFIG!r}, got {ci_inputs.get('config_file')!r}"
        )
    if ci_inputs.get("directory") != EXPECTED_DIRECTORY:
        violations.append(
            f"CI Checkov directory must be {EXPECTED_DIRECTORY!r}, got {ci_inputs.get('directory')!r}"
        )
    if ci_inputs.get("download_external_modules") != "true":
        violations.append(
            "CI Checkov download_external_modules must be true "
            f"(got {ci_inputs.get('download_external_modules')!r})"
        )
    if ci_inputs.get("soft_fail") != "false":
        violations.append(
            f"CI Checkov soft_fail must be false (got {ci_inputs.get('soft_fail')!r})"
        )

    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    violations = check_repo(repo_root)
    if violations:
        for item in violations:
            print(item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
