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

variable "allowed_workflow_refs" {
  description = <<-EOT
    GitHub refs whose OIDC tokens are accepted by the WIF provider. Tokens from
    any other ref are rejected at the provider before the SA binding is consulted
    (ADR-037-R7). Defaults to the two protected integration branches. Override
    only with explicit justification documented in docs/adr/exceptions.yaml.
  EOT
  type        = list(string)
  default     = ["refs/heads/dev", "refs/heads/main"]

  validation {
    condition     = length(var.allowed_workflow_refs) > 0
    error_message = "allowed_workflow_refs must contain at least one ref."
  }
}

variable "build_roles" {
  description = <<-EOT
    Project roles granted to the shared CI build+deploy service account. It runs
    BOTH the packer GCE image builds AND the platform-core Terraform apply/destroy
    (this SA is the only WIF-federated deploy identity), so it needs the roles to
    manage every platform-core resource. This is the scoped enumeration replacing
    the rehearsal-era roles/owner grant (#407); a missing role surfaces as a 403
    during terraform apply/destroy and is added here.
  EOT
  type        = list(string)
  default = [
    # --- Packer GCE image build/export ---
    # compute.admin (superset of instanceAdmin.v1 + storageAdmin) is what the
    # `gcloud compute images export` Cloud Build identity needs: daisy creates
    # and tears down the export worker VM, its disks and snapshots, then writes
    # the GCE image - no narrower predefined role covers that whole lifecycle.
    "roles/compute.admin",
    "roles/iap.tunnelResourceAccessor",
    "roles/storage.admin",
    "roles/cloudbuild.builds.editor",
    # --- GKE control-plane access over Connect Gateway (#1723) ---
    # The gateway roles authorize fleet-membership impersonation; container.admin
    # authorizes the kubectl apply via GKE's IAM->RBAC mapping (the deploy applies
    # cluster-scoped objects: namespaces, CRDs, cluster services). Together these
    # replace the old public-endpoint + runner-IP-allowlist access path.
    "roles/gkehub.editor",
    "roles/gkehub.gatewayEditor",
    "roles/gkehub.viewer",
    "roles/container.admin",
    # --- platform-core Terraform apply/destroy (the full deploy) ---
    "roles/serviceusage.serviceUsageAdmin",  # enable/disable project APIs
    "roles/servicenetworking.networksAdmin", # PSA connection for Cloud SQL/Redis private IP
    "roles/dns.admin",                       # private googleapis managed zones
    "roles/cloudsql.admin",                  # Cloud SQL instance/db/user
    "roles/redis.admin",                     # Memorystore
    "roles/pubsub.admin",                    # messaging topics/subscriptions/DLQ
    "roles/secretmanager.admin",             # runtime secret create/version
    "roles/cloudkms.admin",                  # artifact-registry CMEK keyring/key
    "roles/artifactregistry.admin",          # control-plane image repos
    "roles/identityplatform.admin",          # Identity Platform config
    "roles/monitoring.editor",               # messaging alarms / notification channels
    "roles/iam.serviceAccountAdmin",         # create the workload service accounts
    "roles/resourcemanager.projectIamAdmin", # bind workload SA roles at the project
    # serviceAccountUser (actAs) is deliberately NOT granted project-wide: it trips
    # CKV_GCP_41 and would let the SA run VMs as any identity in the project. The
    # only actAs the platform apply needs is on the GKE node SA (to create the node
    # pools); that is granted resource-scoped in modules/portal/iam
    # (deploy_act_as_gke_nodes), mirroring this module's self-scoped
    # packer_build_act_as_self.
  ]
}
