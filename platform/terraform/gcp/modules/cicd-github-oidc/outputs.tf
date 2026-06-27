output "workload_identity_provider" {
  description = "Full resource name of the GitHub OIDC provider (set as the GCP_WORKLOAD_IDENTITY_PROVIDER GitHub secret)."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "packer_build_service_account_email" {
  description = "Email of the packer build service account (set as the GCP_SERVICE_ACCOUNT GitHub secret)."
  value       = google_service_account.packer_build.email
}
