# GitHub Actions -> GCP federation for the packer GCE image builds
# (packer-gcp.yml). The GCP analog of platform/terraform/global/iam/
# github-oidc.tf (AWS). GitHub's OIDC token is exchanged for short-lived
# credentials that impersonate the packer build service account; no long-lived
# service-account keys are issued.

locals {
  # Restrict federation to this repository's workflows only.
  repo_principal = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_org}/${var.github_repo}"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "Federates ${var.github_org}/${var.github_repo} GitHub Actions for image builds."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  # CKV_GCP_125 wants an exact `assertion.sub ==` pin to a single identity
  # (branch+workflow+env). We enforce the same security intent at the
  # attribute_condition level by restricting assertion.ref to the protected
  # integration branches (ADR-037-R7), which is more maintainable and still
  # rejects tokens from any other branch or fork. The SA's principalSet binding
  # provides the second layer; together they satisfy the spirit of CKV_GCP_125.
  # checkov:skip=CKV_GCP_125:ref-allowlist in attribute_condition + principalSet SA binding achieves equivalent protection without locking to a single sub string; see ADR-037-R7.
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Two-layer hard gate (ADR-037-R7):
  #   1. Repository: tokens from any other repository are rejected before the SA
  #      binding is consulted.
  #   2. Ref: tokens from branches outside the protected integration allowlist
  #      (allowed_workflow_refs) are rejected at the provider. This prevents a
  #      repository collaborator from obtaining cloud credentials by dispatching
  #      a credentialed workflow against a mutable feature branch.
  attribute_condition = "assertion.repository == \"${var.github_org}/${var.github_repo}\" && (${join(" || ", [for r in var.allowed_workflow_refs : "assertion.ref == \"${r}\""])})"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "packer_build" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-packer"
  display_name = "Shifter ${var.environment} packer GCE image builder"
}

# Allow the repo's workflows to impersonate the build SA via the pool.
resource "google_service_account_iam_member" "packer_build_wif" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.repo_principal
}

# The builder VM runs as this same service account, so the build SA needs
# serviceAccountUser on ITSELF (not project-wide, which would let it run VMs as
# any SA and trips CKV_GCP_49). This also satisfies the `actAs` the caller needs
# when it pins both the Cloud Build identity (--cloudbuild-service-account) and
# the export worker VM (--compute-service-account) to this same SA.
resource "google_service_account_iam_member" "packer_build_act_as_self" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.packer_build.email}"
}

# `gcloud compute images export` runs as a Cloud Build job that mints a
# short-lived access token for the export worker's compute service account. With
# the build pinned to this SA (--cloudbuild-service-account), it generates that
# token for itself, so it needs serviceAccountTokenCreator on ITSELF. Scoped to
# this SA, not project-wide (mirrors the provisioner signBlob self-binding).
resource "google_service_account_iam_member" "packer_build_token_creator_self" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.packer_build.email}"
}

resource "google_project_iam_member" "packer_build_roles" {
  # checkov:skip=CKV_GCP_42:A CI GCE image builder requires compute/storage admin to create builder VMs, disks and images; no non-admin predefined role can create images. Scope is one dedicated, federation-only build SA (#615).
  # checkov:skip=CKV_GCP_49:cloudbuild.builds.editor is required for `gcloud compute images export` (runs as a Cloud Build job); the SA only impersonates the Cloud Build agent for image export (#615).
  for_each = toset(var.build_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.packer_build.email}"
}
