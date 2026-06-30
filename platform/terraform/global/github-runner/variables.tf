variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "runner_count" {
  description = "Number of GitHub Actions runners to create"
  type        = number
  default     = 2
}

variable "vpc_id" {
  description = "VPC ID for the runner"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for the runner. Must be in a non-default, runner-isolated network and have outbound egress for GitHub, ECR, SSM, and AWS APIs."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.large"
}

variable "github_org" {
  description = "GitHub organization or username"
  type        = string
  default     = "Brad-Edwards"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "shifter"
}

# ------------------------------------------------------------------------------
# Health monitoring (#292)
# ------------------------------------------------------------------------------

variable "alarm_email" {
  description = "Optional email address subscribed to the runner-alerts SNS topic. Empty disables the email subscription; Slack/Teams can subscribe to the topic separately."
  type        = string
  default     = ""
}

variable "enable_system_auto_recovery" {
  description = "Add an EC2 recover action to the StatusCheckFailed_System alarm. Scoped to system status checks (AWS-hardware faults) only; instance-check, CPU, and runner-service alarms always notify rather than auto-act."
  type        = bool
  default     = true
}

variable "cpu_alarm_threshold" {
  description = "Average CPU utilization percent that, when sustained, alarms as a hang proxy."
  type        = number
  default     = 95
}
