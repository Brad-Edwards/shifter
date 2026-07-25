# Portal root variables - messaging.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# Messaging (SNS/SQS)
# ------------------------------------------------------------------------------


variable "messaging_consumers" {
  description = "List of consumer names for SQS queues"
  type        = list(string)
}

variable "messaging_visibility_timeout_seconds" {
  description = "SQS visibility timeout in seconds"
  type        = number
}

variable "messaging_message_retention_seconds" {
  description = "SQS message retention period in seconds"
  type        = number
}

variable "messaging_enable_dlq" {
  description = "Enable dead letter queues for failed messages"
  type        = bool
}

variable "messaging_dlq_max_receive_count" {
  description = "Number of times a message can be received before moving to DLQ"
  type        = number
}

variable "messaging_dlq_message_retention_seconds" {
  description = "DLQ message retention period in seconds"
  type        = number
}

variable "messaging_enable_alarms" {
  description = "Enable CloudWatch alarms for queue monitoring"
  type        = bool
}

variable "messaging_alarm_queue_depth_threshold" {
  description = "Alarm threshold for approximate number of messages in queue"
  type        = number
}

variable "messaging_alarm_message_age_threshold" {
  description = "Alarm threshold for oldest message age in seconds"
  type        = number
}

variable "messaging_alarm_dlq_threshold" {
  description = "Alarm threshold for messages in DLQ"
  type        = number
}

variable "messaging_alarm_actions" {
  description = "List of ARNs to notify when alarm triggers (e.g., SNS topic ARNs)"
  type        = list(string)
}

# ------------------------------------------------------------------------------
# SES
# ------------------------------------------------------------------------------


variable "email_backend" {
  description = "Django email backend"
  type        = string
  default     = "django_ses.SESBackend"
}

variable "ctf_from_email" {
  description = "From address for CTF emails"
  type        = string
  default     = "ctf@example.com"
}

# Portal runtime capacity tunables (#930). Forwarded to the portal/ssm module,
# which validates them; per-instance terminal cap = portal_web_workers *
# terminal_max_sessions. Set explicitly in terraform.tfvars so event capacity
# policy is visible in one place rather than hidden in the image defaults.
variable "portal_web_workers" {
  description = "Gunicorn/Uvicorn worker processes per portal instance (PORTAL_WEB_WORKERS), sized to instance vCPUs."
  type        = number
  default     = 4
}

variable "terminal_max_sessions" {
  description = "Active terminal SSH sessions per worker process (TERMINAL_MAX_SESSIONS)."
  type        = number
  default     = 200
}

variable "terminal_max_sessions_per_user" {
  description = "Active terminal SSH sessions per user, per worker process (TERMINAL_MAX_SESSIONS_PER_USER)."
  type        = number
  default     = 10
}

variable "terminal_idle_timeout_seconds" {
  description = "Idle terminal session timeout in seconds (TERMINAL_IDLE_TIMEOUT_SECONDS)."
  type        = number
  default     = 1800
}

variable "terminal_max_session_seconds" {
  description = "Hard ceiling on a terminal session lifetime in seconds (TERMINAL_MAX_SESSION_SECONDS)."
  type        = number
  default     = 28800
}

variable "terminal_read_poll_seconds" {
  description = "Idle terminal read-loop poll interval in seconds (TERMINAL_READ_POLL_SECONDS)."
  type        = number
  default     = 30
}

variable "ses_domain" {
  description = "Domain for SES email sending (e.g., example.com)"
  type        = string
}

# ------------------------------------------------------------------------------
# Alerting
# ------------------------------------------------------------------------------


variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}
