# Portal root variables - observability.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------


variable "log_level" {
  description = "Django log level (DEBUG, INFO, WARNING, ERROR). Use DEBUG in dev for detailed event tracing."
  type        = string
  default     = "INFO"
}

# ------------------------------------------------------------------------------
# Log Aggregation
# ------------------------------------------------------------------------------


variable "enable_log_aggregation" {
  description = "Enable log aggregation infrastructure (S3, SQS, Firehose)"
  type        = bool
}

# ------------------------------------------------------------------------------
# Phase 5: Additional Log Sources
# ------------------------------------------------------------------------------


variable "enable_alb_access_logs" {
  description = "Enable ALB access logs to S3"
  type        = bool
}

variable "enable_vpc_flow_logs" {
  description = "Enable VPC flow logs to CloudWatch"
  type        = bool
}

variable "enable_rds_log_exports" {
  description = "Enable RDS CloudWatch log exports"
  type        = bool
}

variable "enable_waf_logging" {
  description = "Enable WAF logging to Firehose"
  type        = bool
}

# ------------------------------------------------------------------------------
# Bedrock Logging
# ------------------------------------------------------------------------------


variable "enable_bedrock_logging" {
  description = "Enable Bedrock model invocation logging to CloudWatch"
  type        = bool
}
