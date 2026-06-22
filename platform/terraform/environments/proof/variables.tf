variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "portal_repository_name" {
  description = "Name for the portal ECR repository"
  type        = string
  default     = "shifter-proof-portal"
}

variable "engine_provisioner_repository_name" {
  description = "Name for the engine provisioner ECR repository"
  type        = string
  default     = "shifter-proof-pulumi-provisioner"
}

variable "guacd_repository_name" {
  description = "Name for the guacd ECR repository"
  type        = string
  default     = "shifter-proof-guacd"
}

variable "guacamole_client_repository_name" {
  description = "Name for the guacamole-client ECR repository"
  type        = string
  default     = "shifter-proof-guacamole-client"
}
