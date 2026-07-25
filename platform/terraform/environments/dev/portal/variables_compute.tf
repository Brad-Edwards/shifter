# Portal root variables - compute.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# EC2
# ------------------------------------------------------------------------------


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

variable "enable_ctfd" {
  description = "Enable a standalone CTFd host in the portal VPC"
  type        = bool
}

variable "ctfd_ami_id" {
  description = "AMI ID for the CTFd instance"
  type        = string
}

variable "ctfd_instance_type" {
  description = "EC2 instance type for CTFd"
  type        = string
}

variable "ctfd_root_volume_size" {
  description = "Root volume size for the CTFd instance in GB"
  type        = number
}

variable "ctfd_root_volume_type" {
  description = "Root volume type for the CTFd instance"
  type        = string
}

variable "ctfd_root_volume_iops" {
  description = "Root volume IOPS for the CTFd instance"
  type        = number
}

variable "ctfd_root_volume_throughput" {
  description = "Root volume throughput in MiB/s for the CTFd instance"
  type        = number
}

variable "ctfd_domain" {
  description = "Public DNS name for the dev CTFd host"
  type        = string
}

variable "ctfd_repo_url" {
  description = "CTFd git repository URL"
  type        = string
}

variable "ctfd_git_ref" {
  description = "Pinned CTFd git ref to deploy"
  type        = string
}

variable "ctfd_docker_compose_version" {
  description = "Pinned Docker Compose release tag for CTFd"
  type        = string
}

variable "ctfd_docker_buildx_version" {
  description = "Pinned Docker Buildx release tag for CTFd"
  type        = string
}

variable "ctfd_ssh_public_key" {
  description = "SSH public key material for direct SSH access to the CTFd host"
  type        = string
  default     = ""
}

variable "ctfd_ssh_allowed_cidrs" {
  description = "Map of allowed SSH source CIDRs for the CTFd host"
  type        = map(string)
  default     = {}
}

# ECR values come from terraform_remote_state.foundation

variable "terraform_state_bucket" {
  description = "S3 bucket hosting Terraform state for this deployment instance"
  type        = string
}

variable "terraform_state_region" {
  description = "AWS region for the Terraform state bucket"
  type        = string
  default     = "us-east-2"
}

# ------------------------------------------------------------------------------
# Autoscaling
# ------------------------------------------------------------------------------


variable "enable_autoscaling" {
  description = "Enable Auto Scaling Group instead of single EC2 instance"
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

variable "asg_min_size" {
  description = "Minimum number of instances in the ASG"
  type        = number
}

variable "asg_max_size" {
  description = "Maximum number of instances in the ASG"
  type        = number
}

variable "asg_desired_capacity" {
  description = "Desired number of instances in the ASG"
  type        = number
}

variable "asg_warm_pool_min_size" {
  description = "Minimum number of pre-initialized portal instances to keep in the ASG warm pool. Set 0 to disable."
  type        = number
}

variable "asg_warm_pool_state" {
  description = "Warm pool instance state. Valid values are Stopped, Running, or Hibernated."
  type        = string

  validation {
    condition     = contains(["Stopped", "Running", "Hibernated"], var.asg_warm_pool_state)
    error_message = "asg_warm_pool_state must be one of: Stopped, Running, Hibernated."
  }
}

variable "scale_up_threshold" {
  description = "Average EC2 CPU percentage that fires the guardrail notification alarm (#940: CPU is a notification, not a scaling action)."
  type        = number
}

# Portal app-saturation autoscaling + observability (#940). Scale-out tracks ALB
# request-path saturation instead of average EC2 CPU.
variable "scale_target_requests_per_target" {
  description = "ALBRequestCountPerTarget target-tracking value: requests per target per minute held steady (primary scale-out signal)."
  type        = number
  default     = 1000
}

variable "scale_target_response_time_seconds" {
  description = "ALB TargetResponseTime (Average, seconds) target-tracking value: the latency/queueing target held steady."
  type        = number
  default     = 0.5
}

variable "worker_busy_ratio_scale_out_threshold" {
  description = "Hottest-worker WorkerBusyRatio above which the additive app-saturation scale-out fires."
  type        = number
  default     = 0.8
}

variable "target_response_time_alarm_threshold_seconds" {
  description = "ALB p95 TargetResponseTime (seconds) above which the latency observability alarm notifies."
  type        = number
  default     = 1.0
}

variable "enable_portal_capacity_alarms" {
  description = "Create the portal capacity CloudWatch alarms and dashboard."
  type        = bool
  default     = true
}

variable "portal_capacity_metrics_enabled" {
  description = "Enable the per-worker Shifter/PortalCapacity metrics emitter (PORTAL_CAPACITY_METRICS_ENABLED)."
  type        = bool
  default     = false
}

variable "portal_worker_soft_concurrency" {
  description = "Busy-ratio denominator: soft concurrent in-flight HTTP request target per portal web worker (PORTAL_WORKER_SOFT_CONCURRENCY)."
  type        = number
  default     = 6
}

# ------------------------------------------------------------------------------
# Long-lived connection lifecycle (#931)
# ------------------------------------------------------------------------------

# Explicit, ordered timing for the portal's long-lived WebSocket / RDP / SSH
# workload. Dev uses shorter drain windows for faster iteration; the ordering
# ws_ping(20s) < idle_timeout, and graceful(30s) < docker_stop < dereg <=
# termination_drain is preserved.

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout (s) for long-lived WebSocket connections (#931)."
  type        = number
  default     = 300
}

variable "portal_deregistration_delay_seconds" {
  description = "Portal target-group deregistration delay (s) for connection drain (#931)."
  type        = number
  default     = 60
}

variable "guacamole_deregistration_delay_seconds" {
  description = "Guacamole target-group deregistration delay (s) for RDP/SSH drain (#931)."
  type        = number
  default     = 60
}

variable "termination_drain_timeout" {
  description = "ASG termination-drain hold (s) for in-flight session drain on refresh/scale-in (#931)."
  type        = number
  default     = 90
}

variable "docker_stop_timeout" {
  description = "Docker stop grace (s) on redeploy; must exceed the 30s Gunicorn graceful timeout (#931)."
  type        = number
  default     = 35
}

variable "instance_refresh_min_healthy_percentage" {
  description = "Minimum healthy percentage kept in service during an ASG instance refresh (#931)."
  type        = number
  default     = 50
}

variable "health_check_type" {
  description = "Portal ASG health-check type: ELB ties refresh readiness to ALB target health; EC2 is a non-ALB fallback (#1639)."
  type        = string
  default     = "ELB"
}

variable "health_check_grace_period" {
  description = "Seconds the portal ASG waits after launch before health checks count; env-owned so dev/proof can shorten the loop (#1639)."
  type        = number
  default     = 900
}

variable "instance_refresh_instance_warmup" {
  description = "Seconds an instance refresh waits for a replacement to warm up before counting it healthy; env-owned (#1639)."
  type        = number
  default     = 900
}

# --- AWS Polaris Bedrock agent credential profile (#1377) ---
# Off by default; populated (via the deploy-secrets tfvars mechanism) only in an
# environment that runs AWS Polaris ranges. Passed into the engine-provisioner
# module, which exposes them as the AWS_POLARIS_AGENT_* task env vars that
# config.load_aws_polaris_agent_config() consumes. An empty main inference-
# profile ARN keeps the feature disabled; an AWS polaris-vm range then fails
# closed rather than falling back to the removed IMDS path.
variable "aws_polaris_agent_region" {
  description = "AWS region for the per-range Polaris Bedrock agent STS + Bedrock calls (#1377). Empty disables the feature."
  type        = string
  default     = ""
}

variable "aws_polaris_agent_main_model_id" {
  description = "Bedrock main model id for the Polaris a14-kali agent (#1377)."
  type        = string
  default     = ""
}

variable "aws_polaris_agent_small_model_id" {
  description = "Bedrock small/fast model id for the Polaris a14-kali agent (#1377)."
  type        = string
  default     = ""
}

variable "aws_polaris_agent_main_inference_profile_arn" {
  description = "Approved Bedrock inference-profile ARN for the main model; the per-range Polaris agent enablement signal (#1377). Empty = disabled."
  type        = string
  default     = ""
}

variable "aws_polaris_agent_small_inference_profile_arn" {
  description = "Approved Bedrock inference-profile ARN for the small/fast model (#1377)."
  type        = string
  default     = ""
}

variable "aws_polaris_agent_main_backing_model_arns" {
  description = "Backing Bedrock foundation-model ARNs for the main inference profile (#1377)."
  type        = list(string)
  default     = []
}

variable "aws_polaris_agent_small_backing_model_arns" {
  description = "Backing Bedrock foundation-model ARNs for the small/fast inference profile (#1377)."
  type        = list(string)
  default     = []
}

variable "aws_polaris_agent_sts_session_duration_seconds" {
  description = "STS AssumeRole session duration (s) for the per-range Polaris agent credential (#1377)."
  type        = number
  default     = 900
}

variable "aws_polaris_agent_refresh_window_seconds" {
  description = "Refresh-before-expiry window (s) for the per-range Polaris agent credential (#1377)."
  type        = number
  default     = 300
}

variable "aces_package_bucket_arn" {
  description = "ARN of the S3 bucket holding object-backed ACES package archives (#1567). Grants the portal role read-only access; set it (with SHIFTER_ACES_PACKAGE_BUCKET on the app) to enable object-backed ACES packages. Empty disables the grant."
  type        = string
  default     = ""
}

variable "aces_package_prefix" {
  description = "Optional key prefix under the ACES package bucket the portal may read (least-privilege scoping)."
  type        = string
  default     = ""
}
