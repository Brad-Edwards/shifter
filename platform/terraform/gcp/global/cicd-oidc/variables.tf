variable "project_id" {
  description = "GCP project that hosts the Workload Identity pool and packer build service account."
  type        = string
}

variable "environment" {
  description = "Environment name (e.g. gcp-dev)."
  type        = string
  default     = "gcp-dev"
}

variable "region" {
  description = "Default provider region."
  type        = string
  default     = "us-central1"
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
  description = "GitHub refs whose OIDC tokens are accepted by the WIF provider (ADR-037-R7). Defaults to the protected integration branches."
  type        = list(string)
  default     = ["refs/heads/dev", "refs/heads/main"]
}
