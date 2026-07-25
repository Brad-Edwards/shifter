# Portal composition inputs - database.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
}

variable "db_apply_immediately" {
  description = "Apply portal RDS modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

variable "db_backup_retention_days" {
  description = "Number of days to retain backups"
  type        = number
}

variable "db_ca_cert_identifier" {
  description = "RDS CA certificate identifier for portal and provisioner database TLS."
  type        = string
}

variable "db_deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
}

variable "db_max_allocated_storage" {
  description = "Maximum storage for autoscaling in GB"
  type        = number
}

variable "db_multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
}

variable "db_name" {
  description = "Name of the database to create"
  type        = string
}

variable "db_skip_final_snapshot" {
  description = "Skip final snapshot on deletion"
  type        = bool
}

variable "db_username" {
  description = "Master username for the database"
  type        = string
}

variable "enable_rds_log_exports" {
  description = "Enable RDS CloudWatch log exports"
  type        = bool
}

variable "guacamole_db_allocated_storage" {
  description = "Allocated storage for Guacamole RDS in GB"
  type        = number
}

variable "guacamole_db_apply_immediately" {
  description = "Apply Guacamole RDS modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

variable "guacamole_db_backup_retention_days" {
  description = "Backup retention days for Guacamole RDS"
  type        = number
}

variable "guacamole_db_ca_cert_identifier" {
  description = "RDS CA certificate identifier for Guacamole database TLS."
  type        = string
}

variable "guacamole_db_deletion_protection" {
  description = "Enable deletion protection for Guacamole RDS"
  type        = bool
}

variable "guacamole_db_engine_version" {
  description = "PostgreSQL engine version for Guacamole"
  type        = string
}

variable "guacamole_db_instance_class" {
  description = "RDS instance class for Guacamole database"
  type        = string
}

variable "guacamole_db_max_allocated_storage" {
  description = "Maximum storage for Guacamole RDS autoscaling in GB"
  type        = number
}

variable "guacamole_db_multi_az" {
  description = "Enable Multi-AZ for Guacamole RDS"
  type        = bool
}

variable "guacamole_db_skip_final_snapshot" {
  description = "Skip final snapshot for Guacamole RDS"
  type        = bool
}

variable "redis_apply_immediately" {
  description = "Apply ElastiCache Redis modifications during the deploy instead of queueing them for the maintenance window."
  type        = bool
}

variable "redis_enable_replication" {
  description = "Enable Redis replication with automatic failover"
  type        = bool
}

variable "redis_engine_version" {
  description = "ElastiCache Redis engine version"
  type        = string
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
}
