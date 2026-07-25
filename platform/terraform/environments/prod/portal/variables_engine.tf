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

# Guacamole
# ------------------------------------------------------------------------------

variable "guacd_image_tag" {
  description = "Docker image tag for guacd"
  type        = string
}

variable "guacamole_client_image_tag" {
  description = "Docker image tag for guacamole-client"
  type        = string
}

variable "guacd_cpu" {
  description = "CPU units for guacd task"
  type        = number
}

variable "guacd_memory" {
  description = "Memory in MB for guacd task"
  type        = number
}

variable "guacamole_client_cpu" {
  description = "CPU units for guacamole-client task"
  type        = number
}

variable "guacamole_client_memory" {
  description = "Memory in MB for guacamole-client task"
  type        = number
}

variable "guacd_desired_count" {
  description = "Desired number of guacd tasks"
  type        = number
}

variable "guacamole_client_desired_count" {
  description = "Desired number of guacamole-client tasks"
  type        = number
}

variable "guacamole_db_instance_class" {
  description = "RDS instance class for Guacamole database"
  type        = string
}

variable "guacamole_db_allocated_storage" {
  description = "Allocated storage for Guacamole RDS in GB"
  type        = number
}

variable "guacamole_db_max_allocated_storage" {
  description = "Maximum storage for Guacamole RDS autoscaling in GB"
  type        = number
}

variable "guacamole_db_engine_version" {
  description = "PostgreSQL engine version for Guacamole"
  type        = string
}

variable "guacamole_db_ca_cert_identifier" {
  description = "RDS CA certificate identifier for Guacamole database TLS."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}

variable "guacamole_db_multi_az" {
  description = "Enable Multi-AZ for Guacamole RDS"
  type        = bool
}

variable "guacamole_db_backup_retention_days" {
  description = "Backup retention days for Guacamole RDS"
  type        = number
}

variable "guacamole_db_deletion_protection" {
  description = "Enable deletion protection for Guacamole RDS"
  type        = bool
}

variable "guacamole_db_skip_final_snapshot" {
  description = "Skip final snapshot for Guacamole RDS"
  type        = bool
}

variable "guacamole_db_apply_immediately" {
  description = "Apply Guacamole RDS modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

variable "guacamole_enable_autoscaling" {
  description = "Enable autoscaling for Guacamole ECS services"
  type        = bool
}

variable "guacamole_autoscaling_min_capacity" {
  description = "Minimum capacity for Guacamole autoscaling"
  type        = number
}

variable "guacamole_autoscaling_max_capacity" {
  description = "Maximum capacity for Guacamole autoscaling"
  type        = number
}

variable "guacamole_autoscaling_cpu_target" {
  description = "CPU target for Guacamole autoscaling"
  type        = number
}

variable "guacamole_secrets_recovery_window_days" {
  description = "Recovery window for Guacamole secrets (0 for dev, 7+ for prod)"
  type        = number
}

variable "guacamole_enable_oidc" {
  description = "Enable OIDC/Cognito authentication for Guacamole"
  type        = bool
}
