# GitHub Actions -> GCP federation for the packer GCE image builds
# (packer-gcp.yml). The GCP analog of platform/terraform/global/iam/
# github-oidc.tf (AWS). GitHub's OIDC token is exchanged for short-lived
# credentials that impersonate the packer build service account; no long-lived
# service-account keys are issued.

locals {
  # Single source of truth: the exact GitHub Actions OIDC subjects trusted to
  # impersonate the shared CI build SA (ADR-004-R23, #1690). Used for BOTH the
  # exact-subject WIF bindings below AND the provider attribute_condition, which
  # MUST stay in sync. Checkov CKV_GCP_125 needs a LITERAL `assertion.sub ==` in
  # the condition, and it does not render `join()`, so the condition is written
  # out statically rather than derived from this list; the check-tf-gcp-wif-trust
  # guard fails the build if the two diverge. Each subject impersonates the SA by
  # its EXACT `sub` (principal://.../subject/<sub>), never a repository-wide
  # principalSet, which would trust every workflow, ref, and actor in the repo.
  # An `environment:` subject does not carry the source branch, so the condition
  # also pins an exact protected `assertion.ref`. The default is the current
  # single-pool caller union; a future per-purpose pool narrows it (#1699).
  federated_subjects = [
    "repo:${var.github_org}/${var.github_repo}:environment:gcp-dev", # _gcp-dev.yml deploy
    "repo:${var.github_org}/${var.github_repo}:environment:dev",     # packer-gcp build/validate (dev)
    "repo:${var.github_org}/${var.github_repo}:environment:proof",   # packer-gcp build/validate (proof)
    "repo:${var.github_org}/${var.github_repo}:environment:prod",    # packer-gcp-promote
    "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/dev",  # gcp-dev-destroy (no environment:)
    "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main", # gcp-dev-destroy (no environment:)
  ]
  wif_subject_principals = {
    for sub in local.federated_subjects :
    sub => "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/${sub}"
  }
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "Federates ${var.github_org}/${var.github_repo} GitHub Actions for image builds."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Exact-subject federation (ADR-004-R23, #1690): a token is accepted only from
  # this repository, from an exact protected branch ref, AND with an allow-listed
  # subject. An `environment:` subject does not carry the source branch, so the
  # ref gate is what denies a feature-branch or tag dispatch that reuses an
  # environment subject; the exact-subject bindings on the SA remain the
  # impersonation boundary. Replaces the repository-only condition + CKV_GCP_125
  # waiver, which trusted every ref/actor in the repo. The subject allow-list is
  # an OR-chain of exact `assertion.sub ==` clauses (not `in [...]`): it is an
  # exact-subject pin AND satisfies CKV_GCP_125, which needs a literal
  # `assertion.sub ==`. CEL literals use single quotes so the HCL string needs no
  # escaping and Checkov's regex matches (hcl2 preserves `\"` literally). Written
  # out statically (not join()) because Checkov cannot render join(); this list
  # MUST equal local.federated_subjects, enforced by check-tf-gcp-wif-trust.
  attribute_condition = "assertion.repository == '${var.github_org}/${var.github_repo}' && assertion.ref in ['refs/heads/dev', 'refs/heads/main'] && (assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:proof' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:prod' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:ref:refs/heads/dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main')"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "packer_build" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-packer"
  display_name = "Shifter ${var.environment} packer GCE image builder"
}

# Allow only the exact allow-listed GitHub Actions subjects to impersonate the
# build SA via the pool - one binding per subject, never a repository-wide
# principalSet (ADR-004-R23, #1690).
resource "google_service_account_iam_member" "packer_build_wif" {
  for_each           = local.wif_subject_principals
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
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
