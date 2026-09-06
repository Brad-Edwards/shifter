output "workload_identity_provider" {
  description = "Active profile's GitHub OIDC provider resource name (GCP_WORKLOAD_IDENTITY_PROVIDER)."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "packer_build_service_account_email" {
  description = "Packer build identity (GCP_PACKER_BUILD_SERVICE_ACCOUNT); federated only in dev/proof."
  value       = google_service_account.packer_build.email
}

output "packer_validate_service_account_email" {
  description = "Packer validation identity (GCP_PACKER_VALIDATE_SERVICE_ACCOUNT); null outside dev/proof."
  value       = try(google_service_account.validate[0].email, null)
}

output "packer_promote_service_account_email" {
  description = "Packer promotion identity (GCP_PACKER_PROMOTE_SERVICE_ACCOUNT); null outside prod."
  value       = try(google_service_account.promote[0].email, null)
}

output "deploy_service_account_email" {
  description = "Platform deploy identity (GCP_DEPLOY_SERVICE_ACCOUNT); null outside gcp-dev."
  value       = try(google_service_account.deploy[0].email, null)
}

output "destroy_service_account_email" {
  description = "Platform destroy identity (GCP_DESTROY_SERVICE_ACCOUNT); null outside gcp-dev."
  value       = try(google_service_account.destroy[0].email, null)
}
