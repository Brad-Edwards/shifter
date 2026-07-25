# Portal root variables - database.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# RDS
# ------------------------------------------------------------------------------


variable "db_name" {
  description = "Name of the database to create"
  type        = string
}

variable "db_username" {
  description = "Master username for the database"
  type        = string
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
}

variable "db_ca_cert_identifier" {
  description = "RDS CA certificate identifier for portal and provisioner database TLS."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
}

variable "db_max_allocated_storage" {
  description = "Maximum storage for autoscaling in GB"
  type        = number
}

variable "db_multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
}

variable "db_backup_retention_days" {
  description = "Number of days to retain backups"
  type        = number
}

variable "db_deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
}

variable "db_skip_final_snapshot" {
  description = "Skip final snapshot on deletion"
  type        = bool
}

variable "redis_apply_immediately" {
  description = "Apply ElastiCache Redis modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

variable "db_apply_immediately" {
  description = "Apply portal RDS modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

# ------------------------------------------------------------------------------
# Redis
# ------------------------------------------------------------------------------


variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
}

variable "redis_engine_version" {
  description = "ElastiCache Redis engine version"
  type        = string
}

variable "redis_enable_replication" {
  description = "Enable Redis replication with automatic failover"
  type        = bool
}
