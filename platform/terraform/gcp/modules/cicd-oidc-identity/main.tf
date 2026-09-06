# GitHub Actions -> GCP federation for purpose-scoped CI identities.

locals {
  image_environment = var.environment == "gcp-dev" ? "dev" : var.environment
  build_enabled     = contains(["gcp-dev", "proof"], var.environment)
  validate_enabled  = contains(["gcp-dev", "proof"], var.environment)
  promote_enabled   = var.environment == "prod"
  deploy_enabled    = var.environment == "gcp-dev"
  destroy_enabled   = var.environment == "gcp-dev"

  # Default GitHub Environment subjects do not include a workflow path. Each
  # purpose therefore has a distinct Environment and a pairwise-disjoint sub.
  purpose_subjects = {
    build = local.build_enabled ? [
      "repo:${var.github_org}/${var.github_repo}:environment:gcp-build-${local.image_environment}",
    ] : []
    validate = local.validate_enabled ? [
      "repo:${var.github_org}/${var.github_repo}:environment:gcp-validate-${local.image_environment}",
    ] : []
    promote = local.promote_enabled ? [
      "repo:${var.github_org}/${var.github_repo}:environment:gcp-promote-prod",
    ] : []
    deploy = local.deploy_enabled ? [
      "repo:${var.github_org}/${var.github_repo}:environment:gcp-dev",
    ] : []
    destroy = local.destroy_enabled ? [
      "repo:${var.github_org}/${var.github_repo}:environment:gcp-dev-destroy",
    ] : []
  }
  federated_subjects = toset(flatten(values(local.purpose_subjects)))
  purpose_subject_principals = {
    for purpose, subjects in local.purpose_subjects : purpose => {
      for sub in subjects :
      sub => "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/${sub}"
    }
  }
  ref_condition               = join(" || ", [for ref in var.allowed_workflow_refs : "assertion.ref == '${ref}'"])
  terraform_state_bucket_name = var.terraform_state_bucket_name == "" ? "${var.project_id}-terraform-state" : var.terraform_state_bucket_name
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions (${var.environment})"
  description               = "Purpose-scoped federation for ${var.github_org}/${var.github_repo}."
}

# Keep the original resource address so existing pools update their trust
# condition in place. Checkov requires literal assertion.sub equality clauses,
# so the profile selector chooses between explicit static CEL strings.
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
  attribute_condition = "assertion.repository == '${var.github_org}/${var.github_repo}' && ${
    var.environment == "gcp-dev" ? "((assertion.ref == 'refs/heads/gcp-dev' && assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-dev') || ((${local.ref_condition}) && (assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-build-dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-validate-dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-dev' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-dev-destroy')))" :
    var.environment == "proof" ? "(${local.ref_condition}) && (assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-build-proof' || assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-validate-proof')" :
    "(${local.ref_condition}) && assertion.sub == 'repo:${var.github_org}/${var.github_repo}:environment:gcp-promote-prod'"
  }"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "packer_build" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-packer"
  display_name = "Shifter ${var.environment} GCE image builder"
}

resource "google_service_account" "validate" {
  count        = local.validate_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-validate"
  display_name = "Shifter ${var.environment} GCE image validator"
}

resource "google_service_account" "promote" {
  count        = local.promote_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-promote"
  display_name = "Shifter prod GCE image promoter"
}

resource "google_service_account" "deploy" {
  count        = local.deploy_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-deploy"
  display_name = "Shifter gcp-dev platform deployer"
}

resource "google_service_account" "destroy" {
  count        = local.destroy_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-destroy"
  display_name = "Shifter gcp-dev platform destroyer"
}

resource "google_service_account_iam_member" "packer_build_wif" {
  for_each           = local.purpose_subject_principals.build
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_service_account_iam_member" "validate_wif" {
  for_each           = local.purpose_subject_principals.validate
  service_account_id = google_service_account.validate[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_service_account_iam_member" "promote_wif" {
  for_each           = local.purpose_subject_principals.promote
  service_account_id = google_service_account.promote[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_service_account_iam_member" "deploy_wif" {
  for_each           = local.purpose_subject_principals.deploy
  service_account_id = google_service_account.deploy[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_service_account_iam_member" "destroy_wif" {
  for_each           = local.purpose_subject_principals.destroy
  service_account_id = google_service_account.destroy[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

resource "google_service_account_iam_member" "packer_build_act_as_self" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.packer_build.email}"
}

resource "google_service_account_iam_member" "packer_build_token_creator_self" {
  service_account_id = google_service_account.packer_build.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.packer_build.email}"
}

resource "google_project_iam_member" "packer_build_roles" {
  for_each = local.build_enabled ? toset(var.build_roles) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.packer_build.email}"
}

# Build inputs are granted by their owning bucket, not through project-wide
# Storage Admin. The exported-image bucket writer lives in packer-build-infra.
resource "google_storage_bucket_iam_member" "packer_build_reader" {
  for_each = local.build_enabled ? var.build_read_bucket_names : toset([])
  bucket   = each.value
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.packer_build.email}"
}

resource "google_project_iam_custom_role" "validate" {
  count       = local.validate_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "${replace(var.name_prefix, "-", "_")}_validate"
  title       = "Shifter GCE image validator"
  description = "Disposable no-SA validation VM lifecycle and exact-candidate evidence labels."
  permissions = var.validate_permissions
}

resource "google_project_iam_member" "validate_roles" {
  for_each = local.validate_enabled ? toset(concat(var.validate_roles, [google_project_iam_custom_role.validate[0].name])) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.validate[0].email}"
}

resource "google_project_iam_custom_role" "promote" {
  count       = local.promote_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "${replace(var.name_prefix, "-", "_")}_promote"
  title       = "Shifter GCE image promoter"
  description = "Verified prod image copy, channel commit, and previous-head deprecation."
  permissions = var.promote_permissions
}

resource "google_project_iam_member" "promote_role" {
  count   = local.promote_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.promote[0].name
  member  = "serviceAccount:${google_service_account.promote[0].email}"
}

resource "google_project_iam_member" "promotion_source_image_reader" {
  count   = var.promotion_reader_service_account_email == "" ? 0 : 1
  project = var.project_id
  role    = "roles/compute.imageUser"
  member  = "serviceAccount:${var.promotion_reader_service_account_email}"
}

resource "google_project_iam_member" "deploy_roles" {
  for_each = local.deploy_enabled ? toset(var.platform_roles) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deploy[0].email}"
}

resource "google_project_iam_member" "destroy_roles" {
  for_each = local.destroy_enabled ? toset(var.platform_roles) : toset([])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.destroy[0].email}"
}


# The foundational root owns CI access to its pre-existing backend bucket.
# These bindings outlive platform-core and replace workflow-time self-grants.
resource "google_storage_bucket_iam_member" "deploy_state_object_admin" {
  count  = local.deploy_enabled ? 1 : 0
  bucket = local.terraform_state_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deploy[0].email}"
}

resource "google_storage_bucket_iam_member" "deploy_state_bucket_reader" {
  count  = local.deploy_enabled ? 1 : 0
  bucket = local.terraform_state_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.deploy[0].email}"
}

resource "google_storage_bucket_iam_member" "destroy_state_object_admin" {
  count  = local.destroy_enabled ? 1 : 0
  bucket = local.terraform_state_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.destroy[0].email}"
}

resource "google_storage_bucket_iam_member" "destroy_state_bucket_reader" {
  count  = local.destroy_enabled ? 1 : 0
  bucket = local.terraform_state_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.destroy[0].email}"
}
