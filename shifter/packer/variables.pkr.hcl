// All variables are required - no defaults to prevent silent bugs

variable "aws_region" {
  type        = string
  description = "AWS region to build AMI in"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type for building (recommend t3.large for faster builds)"
}

variable "ami_prefix" {
  type        = string
  description = "Prefix for AMI names"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID to launch builder in (use empty string for default VPC)"
}

variable "subnet_id" {
  type        = string
  description = "Subnet ID to launch builder in (use empty string for default)"
}

# --- polaris-dc.pkr.hcl only (the pre-promoted Polaris domain controller) ------
variable "dc_domain_name" {
  type        = string
  description = "AD forest domain for the polaris-dc bake."
  default     = "boreas.local"
}

variable "dc_netbios_name" {
  type        = string
  description = "NetBIOS name for the polaris-dc bake."
  default     = "BOREAS"
}

variable "dc_content_script" {
  type        = string
  description = "Path (relative to shifter/packer) to the AD-content seed staged into the polaris-dc image and run post-promotion by dc-content-seed.ps1."
  default     = "../../scripts/polaris-aws-range/a2_setup.ps1"
}
