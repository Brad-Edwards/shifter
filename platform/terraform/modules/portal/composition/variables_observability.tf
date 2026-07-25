# Portal composition inputs - observability.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "enable_bedrock_logging" {
  description = "Enable Bedrock model invocation logging to CloudWatch"
  type        = bool
}

variable "enable_log_aggregation" {
  description = "Enable log aggregation infrastructure (S3, SQS, Firehose)"
  type        = bool
}

variable "firewall_log_retention_days" {
  description = "CloudWatch retention in days for portal Network Firewall FLOW / ALERT logs."
  type        = number
}

variable "log_level" {
  description = "Django log level (DEBUG, INFO, WARNING, ERROR). Use DEBUG in dev for detailed event tracing."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}
