"""Tests for check_checkov_invocation_parity.py.

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
    def test_repo_root_passes_when_precommit_and_ci_match(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        violations = check_repo(repo_root)
        self.assertEqual(violations, [], f"unexpected violations: {violations}")

    def test_missing_download_external_modules_in_precommit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                textwrap.dedent(
                    """
                    repos:
                      - repo: https://github.com/bridgecrewio/checkov
                        hooks:
                          - id: checkov
                            args:
                              [--config-file, platform/terraform/.checkov.yaml, --directory, platform/terraform/]
                    """
                ).lstrip(),
                encoding="utf-8",
            )
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
                              soft_fail: false
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            violations = check_repo(root)

        self.assertTrue(
            any("download-external-modules" in v for v in violations),
            f"expected download-external-modules violation, got: {violations}",
        )

    def test_precommit_soft_fail_arg_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                textwrap.dedent(
                    """
                    repos:
                      - repo: https://github.com/bridgecrewio/checkov
                        hooks:
                          - id: checkov
                            args:
                              [--config-file, platform/terraform/.checkov.yaml, --directory, platform/terraform/, --download-external-modules, --soft-fail]
                    """
                ).lstrip(),
                encoding="utf-8",
            )
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
                              soft_fail: false
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            violations = check_repo(root)

        self.assertTrue(
            any("soft-fail" in v for v in violations),
            f"expected soft-fail violation, got: {violations}",
        )

    def test_ci_soft_fail_true_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                textwrap.dedent(
                    """
                    repos:
                      - repo: https://github.com/bridgecrewio/checkov
                        hooks:
                          - id: checkov
                            args:
                              [--config-file, platform/terraform/.checkov.yaml, --directory, platform/terraform/, --download-external-modules, "true"]
                    """
                ).lstrip(),
                encoding="utf-8",
            )
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

    def test_wrong_config_file_in_precommit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                textwrap.dedent(
                    """
                    repos:
                      - repo: https://github.com/bridgecrewio/checkov
                        hooks:
                          - id: checkov
                            args:
                              [--config-file, wrong/.checkov.yaml, --directory, platform/terraform/, --download-external-modules, "true"]
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                _valid_ci_workflow(),
                encoding="utf-8",
            )
            violations = check_repo(root)

        self.assertTrue(
            any("config-file" in v and "pre-commit" in v for v in violations),
            f"expected pre-commit config violation, got: {violations}",
        )

    def test_wrong_directory_in_precommit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                textwrap.dedent(
                    """
                    repos:
                      - repo: https://github.com/bridgecrewio/checkov
                        hooks:
                          - id: checkov
                            args:
                              [--config-file, platform/terraform/.checkov.yaml, --directory, wrong/, --download-external-modules, "true"]
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (root / ".github").mkdir()
            (root / ".github" / "workflows").mkdir()
            (root / ".github" / "workflows" / "_quality.yml").write_text(
                _valid_ci_workflow(),
                encoding="utf-8",
            )
            violations = check_repo(root)

        self.assertTrue(
            any("directory" in v and "pre-commit" in v for v in violations),
            f"expected pre-commit directory violation, got: {violations}",
        )

    def test_wrong_config_file_in_ci_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(
                _valid_precommit_config(),
                encoding="utf-8",
            )
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
            (root / ".pre-commit-config.yaml").write_text(
                _valid_precommit_config(),
                encoding="utf-8",
            )
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
            (root / ".pre-commit-config.yaml").write_text(
                _valid_precommit_config(),
                encoding="utf-8",
            )
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


    def test_reachable_security_iac_is_resolved_by_graph_not_filename_glob(self) -> None:
        # A disconnected, earlier-sorting decoy with GOOD Checkov args must NOT
        # satisfy the check while the REACHABLE security-iac (in a child called
        # by _quality.yml) is weakened with soft_fail. Filename-glob first-match
        # would validate the decoy; graph resolution catches the real gate (#689).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(_valid_precommit_config(), encoding="utf-8")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "_quality.yml").write_text(
                "jobs:\n  terraform:\n    uses: ./.github/workflows/_quality-terraform.yml\n",
                encoding="utf-8",
            )
            (wf / "_quality-terraform.yml").write_text(
                _security_iac_workflow(soft_fail="true"),  # reachable + weakened
                encoding="utf-8",
            )
            (wf / "_quality-aaa-decoy.yml").write_text(
                _security_iac_workflow(soft_fail="false"),  # unreachable decoy, good args
                encoding="utf-8",
            )
            violations = check_repo(root)
        self.assertTrue(
            any("soft_fail" in v for v in violations),
            f"graph resolution must catch the weakened reachable gate, got: {violations}",
        )

    def test_duplicate_reachable_security_iac_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(_valid_precommit_config(), encoding="utf-8")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "_quality.yml").write_text(
                "jobs:\n"
                "  a:\n    uses: ./.github/workflows/_quality-a.yml\n"
                "  b:\n    uses: ./.github/workflows/_quality-b.yml\n",
                encoding="utf-8",
            )
            (wf / "_quality-a.yml").write_text(_security_iac_workflow(soft_fail="false"), encoding="utf-8")
            (wf / "_quality-b.yml").write_text(_security_iac_workflow(soft_fail="false"), encoding="utf-8")
            violations = check_repo(root)
        self.assertTrue(
            any("multiple reachable" in v for v in violations),
            f"expected a duplicate-invocation violation, got: {violations}",
        )

    def test_no_reachable_security_iac_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(_valid_precommit_config(), encoding="utf-8")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "_quality.yml").write_text(
                "jobs:\n  paths:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n",
                encoding="utf-8",
            )
            (wf / "_quality-aaa-decoy.yml").write_text(  # present but unreachable
                _security_iac_workflow(soft_fail="false"), encoding="utf-8"
            )
            violations = check_repo(root)
        self.assertTrue(
            any("no reachable security-iac" in v for v in violations),
            f"expected a missing-invocation violation, got: {violations}",
        )


    def test_uses_inside_run_scalar_is_not_a_call_edge(self) -> None:
        # A `uses: ./...` line planted inside a run block scalar is not a real
        # call edge; graph resolution must not treat the decoy as reachable.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(_valid_precommit_config(), encoding="utf-8")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "_quality.yml").write_text(
                "jobs:\n"
                "  real:\n    uses: ./.github/workflows/_quality-real.yml\n"
                "  spoof:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
                "          echo 'uses: ./.github/workflows/_quality-decoy.yml'\n",
                encoding="utf-8",
            )
            (wf / "_quality-real.yml").write_text(_security_iac_workflow(soft_fail="true"), encoding="utf-8")
            (wf / "_quality-decoy.yml").write_text(_security_iac_workflow(soft_fail="false"), encoding="utf-8")
            violations = check_repo(root)
        self.assertTrue(
            any("soft_fail" in v for v in violations),
            f"a run-scalar decoy must not shadow the weakened reachable gate, got: {violations}",
        )

    def test_disabled_call_job_does_not_extend_reachability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".pre-commit-config.yaml").write_text(_valid_precommit_config(), encoding="utf-8")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "_quality.yml").write_text(
                "jobs:\n"
                "  real:\n    uses: ./.github/workflows/_quality-real.yml\n"
                "  dead:\n    if: false\n    uses: ./.github/workflows/_quality-decoy.yml\n",
                encoding="utf-8",
            )
            (wf / "_quality-real.yml").write_text(_security_iac_workflow(soft_fail="true"), encoding="utf-8")
            (wf / "_quality-decoy.yml").write_text(_security_iac_workflow(soft_fail="false"), encoding="utf-8")
            violations = check_repo(root)
        self.assertTrue(
            any("soft_fail" in v for v in violations),
            f"a disabled call job must not make the decoy reachable, got: {violations}",
        )


def _security_iac_workflow(*, soft_fail: str) -> str:
    return textwrap.dedent(
        f"""
        jobs:
          security-iac:
            steps:
              - name: Checkov IaC Security
                uses: bridgecrewio/checkov-action@v12
                with:
                  directory: platform/terraform/
                  config_file: platform/terraform/.checkov.yaml
                  download_external_modules: true
                  soft_fail: {soft_fail}
        """
    ).lstrip()


def _valid_precommit_config() -> str:
    return textwrap.dedent(
        """
        repos:
          - repo: https://github.com/bridgecrewio/checkov
            hooks:
              - id: checkov
                args:
                  [--config-file, platform/terraform/.checkov.yaml, --directory, platform/terraform/, --download-external-modules, "true"]
        """
    ).lstrip()


def _valid_ci_workflow() -> str:
    return textwrap.dedent(
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
                  soft_fail: false
        """
    ).lstrip()
