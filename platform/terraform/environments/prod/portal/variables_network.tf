# Portal root variables - network.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# VPC
# ------------------------------------------------------------------------------


variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
}

variable "enable_nat_gateway" {
  description = "Whether to create a NAT gateway for private subnet internet access"
  type        = bool
}

# ------------------------------------------------------------------------------
# ALB
# ------------------------------------------------------------------------------


variable "domain_name" {
  description = "Domain name for ACM certificate (e.g., shifter.example.com)"
  type        = string
}

variable "app_port" {
  description = "Port the Django application listens on"
  type        = number
}

variable "health_check_path" {
  description = "Health check path for ALB target group"
  type        = string
}

# ------------------------------------------------------------------------------
# Portal east-west inspection (#122)
# ------------------------------------------------------------------------------


variable "enable_portal_inspection" {
  description = "Insert an AWS Network Firewall east-west inspection boundary between the portal public (ALB) tier and the private services tier. Requires enable_log_aggregation = true."
  type        = bool
}

variable "firewall_log_retention_days" {
  description = "CloudWatch retention in days for portal Network Firewall FLOW / ALERT logs."
  type        = number
}

variable "portal_inspection_delete_protection" {
  description = "Enable delete protection on the portal inspection Network Firewall. Dev sets false to allow intentional teardown; prod keeps true. Mirrors the db_deletion_protection convention."
  type        = bool
}
