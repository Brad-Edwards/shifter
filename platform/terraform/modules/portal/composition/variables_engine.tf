# Portal composition inputs - engine.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "aws_polaris_agent_main_backing_model_arns" {
  description = "Backing Bedrock foundation-model ARNs for the main inference profile (#1377)."
  type        = list(string)
}

variable "aws_polaris_agent_main_inference_profile_arn" {
  description = "Approved Bedrock inference-profile ARN for the main model; the per-range Polaris agent enablement signal (#1377). Empty = disabled."
  type        = string
}

variable "aws_polaris_agent_main_model_id" {
  description = "Bedrock main model id for the Polaris a14-kali agent (#1377)."
  type        = string
}

variable "aws_polaris_agent_refresh_window_seconds" {
  description = "Refresh-before-expiry window (s) for the per-range Polaris agent credential (#1377)."
  type        = number
}

variable "aws_polaris_agent_region" {
  description = "AWS region for the per-range Polaris Bedrock agent STS + Bedrock calls (#1377). Empty disables the feature."
  type        = string
}

variable "aws_polaris_agent_small_backing_model_arns" {
  description = "Backing Bedrock foundation-model ARNs for the small/fast inference profile (#1377)."
  type        = list(string)
}

variable "aws_polaris_agent_small_inference_profile_arn" {
  description = "Approved Bedrock inference-profile ARN for the small/fast model (#1377)."
  type        = string
}

variable "aws_polaris_agent_small_model_id" {
  description = "Bedrock small/fast model id for the Polaris a14-kali agent (#1377)."
  type        = string
}

variable "aws_polaris_agent_sts_session_duration_seconds" {
  description = "STS AssumeRole session duration (s) for the per-range Polaris agent credential (#1377)."
  type        = number
}

variable "engine_container_image_digest" {
  description = "Immutable Docker image digest for engine provisioner container"
  type        = string
}

variable "engine_container_tag" {
  description = "Docker image tag for engine provisioner container"
  type        = string
}

variable "guacamole_client_cpu" {
  description = "CPU units for guacamole-client task"
  type        = number
}

variable "guacamole_client_desired_count" {
  description = "Desired number of guacamole-client tasks"
  type        = number
}

variable "guacamole_client_image_tag" {
  description = "Docker image tag for guacamole-client"
  type        = string
}

variable "guacamole_client_memory" {
  description = "Memory in MB for guacamole-client task"
  type        = number
}

variable "guacamole_deregistration_delay_seconds" {
  description = "Guacamole target-group deregistration delay (s) for RDP/SSH drain (#931)."
  type        = number
}

variable "guacamole_enable_oidc" {
  description = "Enable OIDC/Cognito authentication for Guacamole"
  type        = bool
}

variable "guacamole_secrets_recovery_window_days" {
  description = "Recovery window for Guacamole secrets (0 for dev, 7+ for prod)"
  type        = number
}

variable "guacd_cpu" {
  description = "CPU units for guacd task"
  type        = number
}

variable "guacd_desired_count" {
  description = "Desired number of guacd tasks"
  type        = number
}

variable "guacd_image_tag" {
  description = "Docker image tag for guacd"
  type        = string
}

variable "guacd_memory" {
  description = "Memory in MB for guacd task"
  type        = number
}
