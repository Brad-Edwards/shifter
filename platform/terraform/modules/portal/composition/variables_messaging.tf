# Portal composition inputs - messaging.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}

variable "allowed_email_domains" {
  description = "List of allowed email domains for signup"
  type        = list(string)
}

variable "allowed_emails" {
  description = "List of specific allowed emails (for external users)"
  type        = list(string)
}

variable "ctf_from_email" {
  description = "From address for CTF emails"
  type        = string
}

variable "email_backend" {
  description = "Django email backend"
  type        = string
}

variable "messaging_alarm_dlq_threshold" {
  description = "Alarm threshold for messages in DLQ"
  type        = number
}

variable "messaging_alarm_message_age_threshold" {
  description = "Alarm threshold for oldest message age in seconds"
  type        = number
}

variable "messaging_alarm_queue_depth_threshold" {
  description = "Alarm threshold for approximate number of messages in queue"
  type        = number
}

variable "messaging_consumers" {
  description = "List of consumer names for SQS queues"
  type        = list(string)
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

variable "messaging_enable_dlq" {
  description = "Enable dead letter queues for failed messages"
  type        = bool
}

variable "messaging_message_retention_seconds" {
  description = "SQS message retention period in seconds"
  type        = number
}

variable "messaging_visibility_timeout_seconds" {
  description = "SQS visibility timeout in seconds"
  type        = number
}

variable "ses_domain" {
  description = "Domain for SES email sending (e.g., example.com)"
  type        = string
}
