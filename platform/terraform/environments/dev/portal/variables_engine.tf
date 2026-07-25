# Portal root variables - engine.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# Provisioner
# ------------------------------------------------------------------------------


variable "victim_instance_type" {
  description = "Instance type for victim EC2 instances"
  type        = string
}

variable "kali_instance_type" {
  description = "Instance type for Kali EC2 instances"
  type        = string
}

# ------------------------------------------------------------------------------
# Engine Provisioner
# ------------------------------------------------------------------------------


variable "engine_container_tag" {
  description = "Docker image tag for engine provisioner container"
  type        = string
  default     = "latest"
}

variable "engine_container_image_digest" {
  description = "Immutable Docker image digest for engine provisioner container"
  type        = string
  default     = ""
}

variable "dc_domain_name" {
  description = "Domain name for prebaked DC (e.g., internal.shifter)"
  type        = string
  default     = "internal.shifter"
}

# The DC Administrator password is intentionally not a Terraform variable.
# It lives in aws_secretsmanager_secret.dc_domain_password (created by
# the engine-provisioner module) with the value managed out-of-band, and
# is plumbed to the engine task via ECS `secrets = [...]` and to the
# portal Django container via the portal/ssm + ec2 modules and
# entrypoint.sh.
