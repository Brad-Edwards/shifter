# Environment variables - NO DEFAULTS

# ------------------------------------------------------------------------------
# General
# ------------------------------------------------------------------------------


variable "environment" {
  description = "Environment name (e.g., prod, dev)"
  type        = string
}

# Renderer-owned backend selection (PLAT-2005). Supplied at deploy time via a
# rendered cloud_provider.auto.tfvars (shifter-config render-runtime), never a
# committed terraform.tfvars literal. No default: a missing tfvar must fail
# the plan loudly instead of silently synthesizing "aws".
variable "cloud_provider" {
  description = "Backend identity ('aws', 'gcp', ...) threaded to the portal ec2 and engine-provisioner module calls. Rendered from shifter.yaml's settings.backend; must not be hardcoded or defaulted here."
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}

variable "tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
}

# ------------------------------------------------------------------------------
# CI Testing
# ------------------------------------------------------------------------------


variable "django_secret_key_ci" {
  description = "Django secret key for CI testing (extracted by quality.yml workflow, not used by Terraform)"
  type        = string
  default     = ""
}
