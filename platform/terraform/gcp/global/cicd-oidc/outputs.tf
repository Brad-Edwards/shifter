output "workload_identity_provider" {
  description = "GitHub OIDC provider resource name; set as the GCP_WORKLOAD_IDENTITY_PROVIDER GitHub secret."
  value       = module.cicd_oidc_identity.workload_identity_provider
}

output "packer_build_service_account_email" {
  description = "Packer/deploy build service account email; set as the GCP_SERVICE_ACCOUNT GitHub secret."
  value       = module.cicd_oidc_identity.packer_build_service_account_email
}
