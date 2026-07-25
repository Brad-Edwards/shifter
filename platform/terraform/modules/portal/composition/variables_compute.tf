# Portal composition inputs - compute.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "app_port" {
  description = "Port the Django application listens on"
  type        = number
}

variable "asg_desired_capacity" {
  description = "Desired number of instances in the ASG"
  type        = number
}

variable "asg_max_size" {
  description = "Maximum number of instances in the ASG"
  type        = number
}

variable "asg_min_size" {
  description = "Minimum number of instances in the ASG"
  type        = number
}

variable "asg_warm_pool_min_size" {
  description = "Minimum number of pre-initialized portal instances to keep in the ASG warm pool. Set 0 to disable."
  type        = number
}

variable "asg_warm_pool_state" {
  description = "Warm pool instance state. Valid values are Stopped, Running, or Hibernated."
  type        = string

}

variable "ec2_ami_id" {
  description = "AMI ID for portal EC2 instances (use standard AL2023, not ECS-optimized)"
  type        = string
}

variable "ec2_instance_type" {
  description = "EC2 instance type for Django portal"
  type        = string
}

variable "ec2_root_volume_size" {
  description = "Size of EC2 root volume in GB"
  type        = number
}

variable "enable_autoscaling" {
  description = "Enable Auto Scaling Group instead of single EC2 instance"
  type        = bool
}

variable "enable_portal_capacity_alarms" {
  description = "Create the portal capacity CloudWatch alarms and dashboard."
  type        = bool
}

variable "guacamole_autoscaling_cpu_target" {
  description = "CPU target for Guacamole autoscaling"
  type        = number
}

variable "guacamole_autoscaling_max_capacity" {
  description = "Maximum capacity for Guacamole autoscaling"
  type        = number
}

variable "guacamole_autoscaling_min_capacity" {
  description = "Minimum capacity for Guacamole autoscaling"
  type        = number
}

variable "guacamole_enable_autoscaling" {
  description = "Enable autoscaling for Guacamole ECS services"
  type        = bool
}

variable "health_check_grace_period" {
  description = "Seconds the portal ASG waits after launch before health checks count; env-owned so dev/proof can shorten the loop (#1639)."
  type        = number
}

variable "health_check_path" {
  description = "Health check path for ALB target group"
  type        = string
}

variable "health_check_type" {
  description = "Portal ASG health-check type: ELB ties refresh readiness to ALB target health; EC2 is a non-ALB fallback (#1639)."
  type        = string
}

variable "instance_refresh_instance_warmup" {
  description = "Seconds an instance refresh waits for a replacement to warm up before counting it healthy; env-owned (#1639)."
  type        = number
}

variable "instance_refresh_min_healthy_percentage" {
  description = "Minimum healthy percentage kept in service during an ASG instance refresh (#931)."
  type        = number
}

variable "kali_instance_type" {
  description = "Instance type for Kali EC2 instances"
  type        = string
}

variable "portal_capacity_metrics_enabled" {
  description = "Enable the per-worker Shifter/PortalCapacity metrics emitter (PORTAL_CAPACITY_METRICS_ENABLED)."
  type        = bool
}

variable "portal_deregistration_delay_seconds" {
  description = "Portal target-group deregistration delay (s) for connection drain (#931)."
  type        = number
}

variable "portal_web_workers" {
  description = "Gunicorn/Uvicorn worker processes per portal instance (PORTAL_WEB_WORKERS), sized to instance vCPUs."
  type        = number
}

variable "portal_worker_soft_concurrency" {
  description = "Busy-ratio denominator: soft concurrent in-flight HTTP request target per portal web worker (PORTAL_WORKER_SOFT_CONCURRENCY)."
  type        = number
}

variable "scale_target_requests_per_target" {
  description = "ALBRequestCountPerTarget target-tracking value: requests per target per minute held steady (primary scale-out signal)."
  type        = number
}

variable "scale_target_response_time_seconds" {
  description = "ALB TargetResponseTime (Average, seconds) target-tracking value: the latency/queueing target held steady."
  type        = number
}

variable "scale_up_threshold" {
  description = "Average EC2 CPU percentage that fires the guardrail notification alarm (#940: CPU is a notification, not a scaling action)."
  type        = number
}

variable "terminal_idle_timeout_seconds" {
  description = "Idle terminal session timeout in seconds (TERMINAL_IDLE_TIMEOUT_SECONDS)."
  type        = number
}

variable "terminal_max_session_seconds" {
  description = "Hard ceiling on a terminal session lifetime in seconds (TERMINAL_MAX_SESSION_SECONDS)."
  type        = number
}

variable "terminal_max_sessions" {
  description = "Active terminal SSH sessions per worker process (TERMINAL_MAX_SESSIONS)."
  type        = number
}

variable "terminal_max_sessions_per_user" {
  description = "Active terminal SSH sessions per user, per worker process (TERMINAL_MAX_SESSIONS_PER_USER)."
  type        = number
}

variable "terminal_read_poll_seconds" {
  description = "Idle terminal read-loop poll interval in seconds (TERMINAL_READ_POLL_SECONDS)."
  type        = number
}

variable "termination_drain_timeout" {
  description = "ASG termination-drain hold (s) for in-flight session drain on refresh/scale-in (#931)."
  type        = number
}

variable "victim_instance_type" {
  description = "Instance type for victim EC2 instances"
  type        = string
}

variable "worker_busy_ratio_scale_out_threshold" {
  description = "Hottest-worker WorkerBusyRatio above which the additive app-saturation scale-out fires."
  type        = number
}
