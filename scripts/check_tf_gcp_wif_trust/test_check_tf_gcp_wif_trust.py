"""Tests for check_tf_gcp_wif_trust.py."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from .check_tf_gcp_wif_trust import check_file

# The exact-subject federation shape this guard requires (ADR-004-R23, #1690):
# a single-source subject list, an exact-subject for_each WIF binding, and a
# static condition (repo + protected ref + literal assertion.sub ==) that matches
# the list. Single-quoted CEL literals so Checkov's regex matches.
GOOD_MODULE = """
locals {
  purpose_subjects = {
    build    = ["repo:Brad-Edwards/shifter:environment:gcp-build-dev"]
    validate = ["repo:Brad-Edwards/shifter:environment:gcp-validate-dev"]
    promote  = ["repo:Brad-Edwards/shifter:environment:gcp-promote-prod"]
    deploy   = ["repo:Brad-Edwards/shifter:environment:gcp-dev"]
    destroy  = ["repo:Brad-Edwards/shifter:environment:gcp-dev-destroy"]
  }
  federated_subjects = toset(flatten(values(local.purpose_subjects)))
  purpose_subject_principals = {
    for purpose, subjects in local.purpose_subjects : purpose => {
      for sub in subjects : sub => "principal://iam.googleapis.com/pool/subject/${sub}"
    }
  }
  ref_condition = join(" || ", [for r in var.allowed_workflow_refs : "assertion.ref == '${r}'"])
}

resource "google_iam_workload_identity_pool_provider" "github" {
  attribute_condition = "assertion.repository == 'Brad-Edwards/shifter' && (${local.ref_condition}) && (assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-build-dev' || assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-validate-dev' || assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-promote-prod' || assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-dev' || assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy')"
}

resource "google_service_account" "packer_build" { account_id = "build" }
resource "google_service_account" "validate" { account_id = "validate" }
resource "google_service_account" "promote" { account_id = "promote" }
resource "google_service_account" "deploy" { account_id = "deploy" }
resource "google_service_account" "destroy" { account_id = "destroy" }

resource "google_service_account_iam_member" "packer_build_wif" {
  for_each           = local.purpose_subject_principals.build
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
resource "google_service_account_iam_member" "validate_wif" {
  for_each           = local.purpose_subject_principals.validate
  service_account_id = google_service_account.validate.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
resource "google_service_account_iam_member" "promote_wif" {
  for_each           = local.purpose_subject_principals.promote
  service_account_id = google_service_account.promote.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
resource "google_service_account_iam_member" "deploy_wif" {
  for_each           = local.purpose_subject_principals.deploy
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
resource "google_service_account_iam_member" "destroy_wif" {
  for_each           = local.purpose_subject_principals.destroy
  service_account_id = google_service_account.destroy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_project_iam_custom_role" "validate" {
  permissions = ["compute.images.get", "compute.images.setLabels"]
}
resource "google_project_iam_custom_role" "promote" {
  permissions = ["compute.images.create", "compute.images.deprecate", "compute.images.get"]
}

variable "build_roles" { default = ["roles/compute.instanceAdmin.v1", "roles/cloudbuild.builds.editor"] }
variable "validate_roles" { default = ["roles/compute.instanceAdmin.v1", "roles/iap.tunnelResourceAccessor"] }
variable "validate_permissions" { default = ["compute.images.get", "compute.images.setLabels"] }
variable "promote_permissions" { default = ["compute.images.create", "compute.images.deprecate", "compute.images.get"] }
variable "deploy_roles" { default = ["roles/compute.admin", "roles/storage.admin"] }
variable "destroy_roles" { default = ["roles/compute.admin", "roles/storage.admin"] }
"""

# Repository-only condition + repository-wide principalSet + surviving waiver.
BAD_MODULE = """
locals {
  repo_principal = "principalSet://iam.googleapis.com/pool/attribute.repository/Brad-Edwards/shifter"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  # checkov:skip=CKV_GCP_125:Federation is repository-scoped the recommended way.
  attribute_condition = "assertion.repository == 'Brad-Edwards/shifter'"
}

resource "google_service_account_iam_member" "packer_build_wif" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.repo_principal
}
"""


# A repository-only condition whose attribute_mapping DOES map assertion.ref /
# assertion.sub. A block-wide token scan would pass this (the tokens appear in
# the mapping); the condition-scoped checks must still reject it (codex #1690).
MAPPED_REPO_ONLY = """
resource "google_iam_workload_identity_pool_provider" "github" {
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == 'Brad-Edwards/shifter'"
}

resource "google_service_account_iam_member" "wif" {
  role   = "roles/iam.workloadIdentityUser"
  member = "principal://iam.googleapis.com/pool/subject/repo:Brad-Edwards/shifter:ref:refs/heads/dev"
}
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).lstrip())
    return path


class CheckTfGcpWifTrustTest(unittest.TestCase):
    def test_exact_subject_module_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", GOOD_MODULE)
            self.assertEqual(check_file(tf), [])

    def test_profile_conditional_static_conditions_pass(self) -> None:
        conditional = GOOD_MODULE.replace(
            '  attribute_condition = "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')"',
            '  attribute_condition = var.environment == "gcp-dev" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')" : var.environment == "proof" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-proof\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-proof\')" : "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\'"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", conditional)
            self.assertEqual(check_file(tf), [])

    def test_one_weakened_profile_condition_is_rejected(self) -> None:
        conditional = GOOD_MODULE.replace(
            '  attribute_condition = "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')"',
            '  attribute_condition = var.environment == "gcp-dev" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')" : var.environment == "proof" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-proof\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-proof\')" : "assertion.repository == \'Brad-Edwards/shifter\' && assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\'"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", conditional)
            reasons = [violation.reason for violation in check_file(tf)]
        self.assertTrue(any("every profile arm" in reason for reason in reasons))

    def test_repository_only_condition_is_rejected(self) -> None:
        missing_ref = GOOD_MODULE.replace(
            " && (${local.ref_condition})",
            "",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", missing_ref)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("exact protected assertion.ref" in reason for reason in reasons))
        self.assertFalse(any("literal assertion.sub" in reason for reason in reasons))

    def test_missing_assertion_sub_clause_is_rejected(self) -> None:
        missing_sub = GOOD_MODULE.replace(
            " && (assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-build-dev' || "
            "assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-validate-dev' || "
            "assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-promote-prod' || "
            "assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-dev' || "
            "assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy')",
            "",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", missing_sub)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("literal assertion.sub" in reason for reason in reasons))
        self.assertFalse(any("exact protected assertion.ref" in reason for reason in reasons))

    def test_profile_arm_with_wrong_exact_subject_set_is_rejected(self) -> None:
        conditional = GOOD_MODULE.replace(
            '  attribute_condition = "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')"',
            '  attribute_condition = var.environment == "gcp-dev" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev-destroy\')" : var.environment == "proof" ? "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-build-proof\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-validate-proof\')" : "assertion.repository == \'Brad-Edwards/shifter\' && (${local.ref_condition}) && (assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-promote-prod\' || assertion.sub == \'repo:Brad-Edwards/shifter:environment:gcp-dev\')"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", conditional)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("wrong exact Environment subjects" in reason for reason in reasons))

    def test_repository_wide_principalset_binding_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", BAD_MODULE)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("principalSet" in reason for reason in reasons))

    def test_surviving_ckv_gcp_125_waiver_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", BAD_MODULE)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("CKV_GCP_125" in reason for reason in reasons))

    def test_condition_binding_drift_is_rejected(self) -> None:
        # local.purpose_subjects lists a subject the static condition omits.
        drift = GOOD_MODULE.replace(
            '    build    = ["repo:Brad-Edwards/shifter:environment:gcp-build-dev"]\n',
            '    build    = ["repo:Brad-Edwards/shifter:environment:gcp-build-dev", '
            '"repo:Brad-Edwards/shifter:environment:gcp-build-stage"]\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", drift)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(
            any("must equal local.purpose_subjects" in r for r in reasons)
        )

    def test_unpaired_gcp_dev_ref_is_rejected(self) -> None:
        widened = GOOD_MODULE.replace(
            "(${local.ref_condition})",
            "(assertion.ref == 'refs/heads/gcp-dev' || ${local.ref_condition})",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", widened)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("paired directly" in reason for reason in reasons))

    def test_gcp_dev_ref_paired_with_environment_subject_passes(self) -> None:
        paired = GOOD_MODULE.replace(
            "(${local.ref_condition}) &&",
            "((assertion.ref == 'refs/heads/gcp-dev' && "
            "assertion.sub == 'repo:Brad-Edwards/shifter:environment:gcp-dev') "
            "|| ((${local.ref_condition}) &&",
        ).replace(
            "'repo:Brad-Edwards/shifter:ref:refs/heads/dev'))",
            "'repo:Brad-Edwards/shifter:ref:refs/heads/dev')))",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", paired)
            self.assertEqual(check_file(tf), [])

    def test_prose_mentioning_ckv_gcp_125_does_not_false_positive(self) -> None:
        # Non-false-positive counterpart to the waiver-rejection test: a comment
        # that NAMES the rule (not a checkov:skip directive) must not trip the
        # CKV_GCP_125 guard, mirroring the real module's explanatory comment.
        module = GOOD_MODULE.replace(
            'resource "google_iam_workload_identity_pool_provider" "github" {',
            'resource "google_iam_workload_identity_pool_provider" "github" {\n'
            "  # Replaces the repository-only condition and the CKV_GCP_125 waiver.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", module)
            self.assertEqual(check_file(tf), [])

    def test_exact_principal_member_not_confused_with_principalset(self) -> None:
        # Non-false-positive counterpart to the principalSet-rejection test: an
        # exact `principal://.../subject/` member contains `//` but is NOT a
        # repository-wide principalSet, and `//` must not be stripped as a comment.
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", GOOD_MODULE)
            reasons = [v.reason for v in check_file(tf)]
        self.assertFalse(any("principalSet" in reason for reason in reasons))

    def test_condition_checks_are_scoped_to_condition_value(self) -> None:
        # attribute_mapping maps assertion.ref/sub, but the condition is repo-only;
        # the checks must fire on the CONDITION, not the block (codex #1690).
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", MAPPED_REPO_ONLY)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("assertion.ref" in r for r in reasons))
        self.assertTrue(any("assertion.sub" in r for r in reasons))

    def test_module_without_wif_resources_is_ignored(self) -> None:
        module = """
        resource "google_storage_bucket" "b" {
          name = "x"
        }
        """
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", module)
            self.assertEqual(check_file(tf), [])

    def test_overlapping_purpose_subject_is_rejected(self) -> None:
        overlap = GOOD_MODULE.replace(
            "environment:gcp-validate-dev",
            "environment:gcp-build-dev",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", overlap)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("pairwise disjoint" in reason for reason in reasons))

    def test_legacy_service_account_without_purpose_map_is_rejected(self) -> None:
        legacy = MAPPED_REPO_ONLY + '\nresource "google_service_account" "packer_build" { account_id = "build" }\n'
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", legacy)
            reasons = [violation.reason for violation in check_file(tf)]
        self.assertTrue(any("five purpose-specific" in reason for reason in reasons))

    def test_cross_purpose_wif_binding_is_rejected(self) -> None:
        crossed = GOOD_MODULE.replace(
            "local.purpose_subject_principals.validate",
            "local.purpose_subject_principals.build",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", crossed)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("validate WIF binding" in reason for reason in reasons))

    def test_validate_broad_roles_are_rejected(self) -> None:
        broad = GOOD_MODULE.replace(
            'variable "validate_roles" { default = ["roles/compute.instanceAdmin.v1", "roles/iap.tunnelResourceAccessor"] }',
            'variable "validate_roles" { default = ["roles/compute.admin", "roles/storage.admin"] }',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", broad)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("validate role set" in reason for reason in reasons))

    def test_validate_permissions_cannot_create_images(self) -> None:
        broad = GOOD_MODULE.replace(
            'variable "validate_permissions" { default = ["compute.images.get", "compute.images.setLabels"] }',
            'variable "validate_permissions" { default = ["compute.images.get", "compute.images.create"] }',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", broad)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("validate permission set" in reason for reason in reasons))

    def test_promote_permissions_cannot_manage_instances(self) -> None:
        broad = GOOD_MODULE.replace(
            'variable "promote_permissions" { default = ["compute.images.create", "compute.images.deprecate", "compute.images.get"] }',
            'variable "promote_permissions" { default = ["compute.images.create", "compute.instances.create"] }',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", broad)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("promote permission set" in reason for reason in reasons))

    def test_build_roles_cannot_have_project_wide_storage_admin(self) -> None:
        broad = GOOD_MODULE.replace(
            'variable "build_roles" { default = ["roles/compute.instanceAdmin.v1", "roles/cloudbuild.builds.editor"] }',
            'variable "build_roles" { default = ["roles/compute.instanceAdmin.v1", "roles/storage.admin"] }',
        )
        with tempfile.TemporaryDirectory() as tmp:
            tf = _write(Path(tmp), "main.tf", broad)
            reasons = [v.reason for v in check_file(tf)]
        self.assertTrue(any("build role set" in reason for reason in reasons))

    def test_non_tf_inputs_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "image.tar"
            artifact.write_bytes(b"\x00\x8a\xff")
            self.assertEqual(check_file(artifact), [])

    def test_purpose_module_missing_explicit_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp) / "cicd-oidc-identity"
            module_dir.mkdir()
            outputs = _write(
                module_dir,
                "outputs.tf",
                'output "workload_identity_provider" { value = "provider" }\n',
            )
            reasons = [violation.reason for violation in check_file(outputs)]
        self.assertTrue(any("explicit purpose outputs" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
