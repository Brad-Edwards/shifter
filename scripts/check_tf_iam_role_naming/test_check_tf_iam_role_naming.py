"""Tests for check_tf_iam_role_naming.py."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from .check_tf_iam_role_naming import check_file


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip())
    return path


class CheckTfIamRoleNamingTest(unittest.TestCase):
    def test_iam_role_using_name_prefix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(
                Path(tmp),
                "main.tf",
                """
                resource "aws_iam_role" "this" {
                  name = "${var.name_prefix}-ec2-role"
                }
                """,
            )
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("iam_name_prefix" in reason for reason in reasons))

    def test_iam_role_using_iam_name_prefix_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(
                Path(tmp),
                "main.tf",
                """
                resource "aws_iam_role" "this" {
                  name = "${local.iam_name_prefix}-ec2-role"
                }
                """,
            )
            self.assertEqual(check_file(tf), [])

    def test_non_tf_inputs_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "lambda.zip"
            artifact.write_bytes(b"\x00\x8a\xff")

            self.assertEqual(check_file(artifact), [])

    def test_github_oidc_legacy_patterns_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(
                Path(tmp),
                "github-oidc.tf",
                """
                resource "aws_iam_policy" "iam_scoped" {
                  policy = jsonencode({
                    Statement = [{
                      Resource = ["arn:aws:iam::123:role/dev-portal-*"]
                    }]
                  })
                }
                """,
            )
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("legacy dev-portal" in reason for reason in reasons))

    def test_github_oidc_too_many_attachments_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attachments = "\n".join(
                f'''
                resource "aws_iam_role_policy_attachment" "p{n}" {{
                  role       = aws_iam_role.github_actions.name
                  policy_arn = aws_iam_policy.p{n}.arn
                }}
                '''
                for n in range(7)
            )
            tf = _write(Path(tmp), "github-oidc.tf", attachments)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("at most" in reason for reason in reasons))

    def test_github_oidc_attachments_within_cap_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attachments = "\n".join(
                f'''
                resource "aws_iam_role_policy_attachment" "p{n}" {{
                  role       = aws_iam_role.github_actions.name
                  policy_arn = aws_iam_policy.p{n}.arn
                }}
                '''
                for n in range(5)
            )
            tf = _write(Path(tmp), "github-oidc.tf", attachments)
            reasons = [v.reason for v in check_file(tf)]
        self.assertFalse(any("at most" in reason for reason in reasons))

    def test_github_oidc_oversized_policy_doc_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            actions = ",\n".join(
                f'"service:VeryLongActionName{n:04d}"' for n in range(300)
            )
            tf = _write(
                Path(tmp),
                "github-oidc.tf",
                f"""
                resource "aws_iam_policy" "management" {{
                  policy = jsonencode({{
                    Statement = [{{
                      Action = [
                        {actions}
                      ]
                      Resource = "*"
                    }}]
                  }})
                }}
                """,
            )
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("size limit" in reason for reason in reasons))

    def test_github_oidc_policy_doc_within_limit_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(
                Path(tmp),
                "github-oidc.tf",
                """
                resource "aws_iam_policy" "management" {
                  policy = jsonencode({
                    Statement = [{
                      Action   = ["logs:CreateLogGroup", "logs:DeleteLogGroup"]
                      Resource = "*"
                    }]
                  })
                }
                """,
            )
            reasons = [v.reason for v in check_file(tf)]
        self.assertFalse(any("size limit" in reason for reason in reasons))

    def test_github_oidc_shifter_pattern_with_attach_allowlist_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(
                Path(tmp),
                "github-oidc.tf",
                """
                resource "aws_iam_policy" "iam_scoped" {
                  policy = jsonencode({
                    Statement = [
                      {
                        Action = ["iam:PutRolePolicy"]
                        Resource = "arn:aws:iam::123:role/shifter-*"
                      },
                      {
                        Action = ["iam:AttachRolePolicy"]
                        Resource = "arn:aws:iam::123:role/shifter-*"
                        Condition = {
                          ArnEquals = {
                            "iam:PolicyArn" = [
                              "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                              "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                              "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                              "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
                            ]
                          }
                        }
                      }
                    ]
                  })
                }
                """,
            )
            self.assertEqual(check_file(tf), [])


class CheckTfImageRoleTest(unittest.TestCase):
    """Base-image-pipeline role invariants (#1656, ADR-004-R22)."""

    _EXACT_PASSROLE_POLICY = """
        resource "aws_iam_role_policy" "image_pipeline" {
          policy = jsonencode({
            Statement = [{
              Action   = ["iam:PassRole"]
              Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-range-range-instance"
              Condition = {
                StringEquals = {
                  "iam:PassedToService" = "ec2.amazonaws.com"
                }
              }
            }]
          })
        }
    """

    def _oidc(self, body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "github-oidc.tf", body)
            return [v.reason for v in check_file(tf)]

    def test_image_role_exact_subject_and_passrole_pass(self) -> None:
        reasons = self._oidc(
            """
            resource "aws_iam_role" "github_actions_image" {
              assume_role_policy = jsonencode({
                Statement = [{
                  Condition = {
                    StringEquals = {
                      "token.actions.githubusercontent.com:sub" = [
                        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/dev",
                        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"
                      ]
                    }
                  }
                }]
              })
            }
            """
            + self._EXACT_PASSROLE_POLICY
        )
        self.assertFalse(any("image-pipeline" in reason for reason in reasons))

    def test_image_role_wildcard_subject_rejected(self) -> None:
        reasons = self._oidc(
            """
            resource "aws_iam_role" "github_actions_image" {
              assume_role_policy = jsonencode({
                Statement = [{
                  Condition = {
                    StringEquals = {
                      "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${var.github_repo}:*"
                    }
                  }
                }]
              })
            }
            """
            + self._EXACT_PASSROLE_POLICY
        )
        self.assertTrue(
            any("not a repo:...:* wildcard" in reason for reason in reasons)
        )

    def test_image_passrole_wildcard_resource_rejected(self) -> None:
        reasons = self._oidc(
            """
            resource "aws_iam_role_policy" "image_pipeline" {
              policy = jsonencode({
                Statement = [{
                  Action   = ["iam:PassRole"]
                  Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*"
                  Condition = {
                    StringEquals = {
                      "iam:PassedToService" = "ec2.amazonaws.com"
                    }
                  }
                }]
              })
            }
            """
        )
        self.assertTrue(any("wildcard resource" in reason for reason in reasons))

    def test_image_passrole_missing_service_condition_rejected(self) -> None:
        reasons = self._oidc(
            """
            resource "aws_iam_role_policy" "image_pipeline" {
              policy = jsonencode({
                Statement = [{
                  Action   = ["iam:PassRole"]
                  Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-range-range-instance"
                }]
              })
            }
            """
        )
        self.assertTrue(any("PassedToService" in reason for reason in reasons))

    def test_deploy_only_oidc_file_skips_image_checks(self) -> None:
        # No github_actions_image role / image_pipeline policy: the new checks
        # must no-op so deploy-only files are unaffected.
        reasons = self._oidc(
            """
            resource "aws_iam_role_policy_attachment" "compute" {
              role       = aws_iam_role.github_actions.name
              policy_arn = aws_iam_policy.compute.arn
            }
            """
        )
        self.assertFalse(any("image-pipeline" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
