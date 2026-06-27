variable "project_id" {
  description = "GCP project that hosts the Workload Identity pool and packer build service account."
  type        = string
}

variable "environment" {
  description = "Environment name (dev or prod)."
  type        = string
}

variable "name_prefix" {
  description = "Resource name prefix (e.g. shifter-gcp-dev)."
  type        = string
}

variable "github_org" {
  description = "GitHub organization that owns the repository allowed to federate."
  type        = string
  default     = "Brad-Edwards"
}

variable "github_repo" {
  description = "GitHub repository allowed to federate into the build service account."
  type        = string
  default     = "shifter"
}

variable "labels" {
  description = "Resource labels."
  type        = map(string)
  default     = {}
}

variable "build_roles" {
  description = <<-EOT
    Project roles granted to the packer build service account. The GCE image
    build needs to create/manage builder VMs, run them as a service account,
    write GCE images, reach the builder over IAP (internal-IP builds, no
    external IP per the org policy), and export images to GCS via Cloud Build.
  EOT
  type        = list(string)
  default = [
    "roles/compute.instanceAdmin.v1",
    "roles/compute.storageAdmin",
    "roles/iap.tunnelResourceAccessor",
    "roles/storage.admin",
    "roles/cloudbuild.builds.editor",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}
