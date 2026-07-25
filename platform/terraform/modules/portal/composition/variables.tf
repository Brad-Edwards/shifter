# Portal composition inputs - general.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
}

variable "cloud_provider" {
  description = "Backend identity ('aws', 'gcp', ...) threaded to the portal ec2 and engine-provisioner module calls. Rendered from shifter.yaml's settings.backend; must not be hardcoded or defaulted here."
  type        = string
}

variable "docker_stop_timeout" {
  description = "Docker stop grace (s) on redeploy; must exceed the 30s Gunicorn graceful timeout (#931)."
  type        = number
}

variable "enable_ctfd" {
  description = "Enable a standalone CTFd host in the portal VPC"
  type        = bool
}

variable "enable_redis" {
  description = <<-EOT
    Wire Redis as the Django Channels backend for the portal runtime
    (ADR-018, #849). Environment-owned and INDEPENDENT of enable_autoscaling:
    a single-instance dev portal may use Redis, and an environment may disable
    Redis to save cost without changing ASG posture. When true, the Redis
    endpoint is published to SSM and the container runs with
    CHANNEL_LAYER_BACKEND=redis (fail-closed if the endpoint is missing); when
    false, the portal runs CHANNEL_LAYER_BACKEND=in_memory.
  EOT
  type        = bool
}

variable "environment" {
  description = "Environment name (e.g., prod, dev)"
  type        = string
}

variable "tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
}

variable "target_response_time_alarm_threshold_seconds" {
  description = "ALB p95 TargetResponseTime (seconds) above which the latency observability alarm notifies."
  type        = number
}
