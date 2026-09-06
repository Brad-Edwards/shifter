# Foundational GitHub Actions -> GCP OIDC/WIF identity root (issue #615 follow-up).
#
# The WIF pool/provider and the packer/deploy build service account are the
# credentials CI authenticates AS. They must OUTLIVE the platform: a
# `gcp-dev-destroy` tears down the platform-core root, and the subsequent CI
# rebuild has to authenticate (via this WIF) to run at all. So this identity
# lives in its own Terraform root with a state prefix separate from the platform
# root -- exactly like platform/terraform/gcp/global/github-runner -- and the
# platform destroy never touches it. This matches the README "Fresh GCP Account
# Order" step 2, where WIF is configured before (and independently of) the
# platform deploy.
#
# The network-coupled packer build-infra (builder subnet, IAP firewall, image
# bucket) stays in the platform-core root because it depends on the platform VPC
# and Cloud NAT; it references this SA by its deterministic email.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "cicd_oidc_identity" {
  source = "../../modules/cicd-oidc-identity"

  project_id            = var.project_id
  environment           = var.environment
  name_prefix           = "shifter-${var.environment}"
  github_org            = var.github_org
  github_repo           = var.github_repo
  allowed_workflow_refs = var.allowed_workflow_refs
}
