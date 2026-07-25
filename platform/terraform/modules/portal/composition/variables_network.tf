# Portal composition inputs - network.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout (s) for long-lived WebSocket connections (#931)."
  type        = number
}

variable "dc_domain_name" {
  description = "Domain name for prebaked DC (e.g., internal.shifter)"
  type        = string
}

variable "domain_name" {
  description = "Domain name for ACM certificate (e.g., shifter.example.com)"
  type        = string
}

variable "enable_alb_access_logs" {
  description = "Enable ALB access logs to S3"
  type        = bool
}

variable "enable_nat_gateway" {
  description = "Whether to create a NAT gateway for private subnet internet access"
  type        = bool
}

variable "enable_portal_inspection" {
  description = "Insert an AWS Network Firewall east-west inspection boundary between the portal public (ALB) tier and the private services tier. Requires enable_log_aggregation = true."
  type        = bool
}

variable "enable_vpc_flow_logs" {
  description = "Enable VPC flow logs to CloudWatch"
  type        = bool
}

variable "enable_waf_logging" {
  description = "Enable WAF logging to Firehose"
  type        = bool
}

variable "portal_inspection_delete_protection" {
  description = "Enable delete protection on the portal inspection Network Firewall. Dev sets false to allow intentional teardown; prod keeps true. Mirrors the db_deletion_protection convention."
  type        = bool
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}
