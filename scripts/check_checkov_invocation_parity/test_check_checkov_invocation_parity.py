"""Tests for the authoritative CI Checkov invocation guard.

Run from the repo root:
    python3 -m unittest scripts.check_checkov_invocation_parity.test_check_checkov_invocation_parity -v
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from .check_checkov_invocation_parity import check_repo


class CheckCheckovInvocationParityTest(unittest.TestCase):
    def test_repo_root_passes_when_ci_uses_canonical_blocking_invocation(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        violations = check_repo(repo_root)
        self.assertEqual(violations, [], f"unexpected violations: {violations}")

    def test_missing_quality_workflow_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            violations = check_repo(Path(tmp))

        self.assertEqual(violations, ["missing .github/workflows/_quality.yml"])

    def test_ci_soft_fail_true_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                textwrap.dedent(
                    """
                    jobs:
                      security-iac:
                        steps:
                          - name: Checkov IaC Security
                            uses: bridgecrewio/checkov-action@v12
                            with:
                              directory: platform/terraform/
                              config_file: platform/terraform/.checkov.yaml
                              download_external_modules: true
                              soft_fail: true
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            violations = check_repo(root)

        self.assertTrue(
            any("soft_fail" in v for v in violations),
            f"expected soft_fail violation, got: {violations}",
        )

    def test_wrong_config_file_in_ci_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                textwrap.dedent(
                    """
                    jobs:
                      security-iac:
                        steps:
                          - name: Checkov IaC Security
                            uses: bridgecrewio/checkov-action@v12
                            with:
                              directory: platform/terraform/
                              config_file: wrong/.checkov.yaml
                              download_external_modules: true
                              soft_fail: false
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            violations = check_repo(root)

        self.assertTrue(
            any("config_file" in v for v in violations),
            f"expected CI config_file violation, got: {violations}",
        )

    def test_wrong_directory_in_ci_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                textwrap.dedent(
                    """
                    jobs:
                      security-iac:
                        steps:
                          - name: Checkov IaC Security
                            uses: bridgecrewio/checkov-action@v12
                            with:
                              directory: wrong/
                              config_file: platform/terraform/.checkov.yaml
                              download_external_modules: true
                              soft_fail: false
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            violations = check_repo(root)

        self.assertTrue(
            any("directory" in v and "CI" in v for v in violations),
            f"expected CI directory violation, got: {violations}",
        )

    def test_missing_download_external_modules_in_ci_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                textwrap.dedent(
                    """
                    jobs:
                      security-iac:
                        steps:
                          - name: Checkov IaC Security
                            uses: bridgecrewio/checkov-action@v12
                            with:
                              directory: platform/terraform/
                              config_file: platform/terraform/.checkov.yaml
                              download_external_modules: false
                              soft_fail: false
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            violations = check_repo(root)

        self.assertTrue(
            any("download_external_modules" in v for v in violations),
            f"expected CI download_external_modules violation, got: {violations}",
        )
