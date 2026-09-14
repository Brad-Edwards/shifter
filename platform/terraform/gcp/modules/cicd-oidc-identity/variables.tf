variable "project_id" {
  description = "GCP project that hosts this environment's WIF provider and purpose identities."
  type        = string
}

variable "environment" {
  description = "Identity-root profile: gcp-dev, proof, or prod."
  type        = string

  validation {
    condition     = contains(["gcp-dev", "proof", "prod"], var.environment)
    error_message = "environment must be gcp-dev, proof, or prod."
  }
}

variable "name_prefix" {
  description = "Resource name prefix (for example shifter-gcp-dev)."
  type        = string
}

variable "region" {
  description = "Region for the private release-evidence bucket."
  type        = string
}

variable "github_org" {
  description = "GitHub organization that owns the repository allowed to federate."
  type        = string
  default     = "Brad-Edwards"
}

variable "github_repo" {
  description = "GitHub repository allowed to federate into the purpose identities."
  type        = string
  default     = "shifter"
}

variable "allowed_workflow_refs" {
  description = "Full protected refs accepted by image, destroy, and ordinary deploy paths."
  type        = list(string)
  default     = ["refs/heads/dev", "refs/heads/main"]

  validation {
    condition = length(var.allowed_workflow_refs) > 0 && alltrue([
      for ref in var.allowed_workflow_refs : contains(["refs/heads/dev", "refs/heads/main"], ref)
    ])
    error_message = "allowed_workflow_refs may contain only refs/heads/dev and refs/heads/main."
  }
}

variable "build_roles" {
  description = "Predefined roles exercised by the Packer build/export identity."
  type        = list(string)
  default = [
    "roles/compute.instanceAdmin.v1",
    "roles/compute.storageAdmin",
    "roles/iap.tunnelResourceAccessor",
    "roles/cloudbuild.builds.editor",
  ]
}

variable "build_read_bucket_names" {
  description = "Existing input buckets the Packer build identity may read, such as the Polaris stack bucket."
  type        = set(string)
  default     = []
}

variable "validate_roles" {
  description = "Predefined roles exercised by the no-SA validation VM path."
  type        = list(string)
  default = [
    "roles/iap.tunnelResourceAccessor",
  ]
}

variable "validate_permissions" {
  description = "Custom-role permissions for exact-candidate validation."
  type        = list(string)
  default = [
    "compute.disks.create",
    "compute.disks.delete",
    "compute.disks.get",
    "compute.disks.use",
    "compute.disks.useReadOnly",
    "compute.images.get",
    "compute.images.getFromFamily",
    "compute.images.setLabels",
    "compute.images.useReadOnly",
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.attachDisk",
    "compute.instances.get",
    "compute.instances.reset",
    "compute.instances.setTags",
    "compute.machineTypes.get",
    "compute.networks.get",
    "compute.networks.use",
    "compute.subnetworks.get",
    "compute.subnetworks.use",
    "compute.zoneOperations.get",
    "compute.zones.get",
    "resourcemanager.projects.get",
    "serviceusage.services.use",
  ]
}

variable "promote_permissions" {
  description = "Custom-role permissions for prod image copy and verified family commit."
  type        = list(string)
  default = [
    "compute.globalOperations.get",
    "compute.images.create",
    "compute.images.deprecate",
    "compute.images.get",
    "compute.images.getFromFamily",
    "compute.images.list",
    "compute.images.setLabels",
    "compute.images.update",
    "compute.images.useReadOnly",
    "resourcemanager.projects.get",
    "serviceusage.services.use",
  ]
}

variable "deploy_roles" {
  description = "Platform-core roles granted only to the deploy identity."
  type        = list(string)
  default = [
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/gkehub.editor",
    "roles/gkehub.gatewayEditor",
    "roles/gkehub.viewer",
    "roles/container.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/servicenetworking.networksAdmin",
    "roles/dns.admin",
    "roles/cloudsql.admin",
    "roles/redis.admin",
    "roles/pubsub.admin",
    "roles/secretmanager.admin",
    "roles/cloudkms.admin",
    "roles/artifactregistry.admin",
    "roles/identityplatform.admin",
    "roles/monitoring.editor",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
  ]
}

variable "destroy_roles" {
  description = "Platform-core teardown roles granted only to the destroy identity."
  type        = list(string)
  default = [
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/gkehub.editor",
    "roles/gkehub.gatewayEditor",
    "roles/gkehub.viewer",
    "roles/container.admin",
    "roles/servicenetworking.networksAdmin",
    "roles/dns.admin",
    "roles/cloudsql.admin",
    "roles/redis.admin",
    "roles/pubsub.admin",
    "roles/secretmanager.admin",
    "roles/cloudkms.admin",
    "roles/artifactregistry.admin",
    "roles/identityplatform.admin",
    "roles/monitoring.editor",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
  ]
}

variable "deploy_storage_permissions" {
  description = "Storage permissions for Terraform-managed platform buckets, bound only to their deterministic resource names."
  type        = list(string)
  default = [
    "storage.buckets.create",
    "storage.buckets.delete",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "storage.buckets.setIamPolicy",
    "storage.buckets.update",
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
    "storage.objects.update",
  ]
}

variable "destroy_storage_permissions" {
  description = "Storage permissions needed to tear down Terraform-managed platform buckets, bound only to their deterministic resource names."
  type        = list(string)
  default = [
    "storage.buckets.delete",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "storage.buckets.setIamPolicy",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
  ]
}

variable "promotion_reader_service_account_email" {
  description = "Prod promote SA email granted read-only access to source images by the source-project root."
  type        = string
  default     = ""

  validation {
    condition = var.promotion_reader_service_account_email == "" || can(regex(
      "^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$",
      var.promotion_reader_service_account_email,
    ))
    error_message = "promotion_reader_service_account_email must be empty or a service-account email."
  }
}

variable "terraform_state_bucket_name" {
  description = "Existing GCS backend bucket receiving resource-scoped deploy/destroy access; defaults to <project>-terraform-state."
  type        = string
  default     = ""
}

variable "platform_external_bucket_names" {
  description = "Existing content buckets whose IAM memberships are managed by platform-core; each receives exact bucket-level lifecycle IAM administration."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for name in var.platform_external_bucket_names :
      length(trimspace(name)) > 0 && lower(name) != lower("${var.project_id}-release-evidence")
    ])
    error_message = "platform_external_bucket_names must contain non-empty names and must not include this identity root's release-evidence bucket."
  }
}
