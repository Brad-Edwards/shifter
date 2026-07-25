# Values read from terraform_remote_state by the environment roots and
# mapped explicitly into typed inputs here, so the composition never
# receives an opaque remote-state object (#688).

variable "foundation_engine_provisioner_ecr_url" {
  description = "foundation remote-state output: engine_provisioner_ecr_url"
  type        = string
}

variable "foundation_guacamole_client_ecr_arn" {
  description = "foundation remote-state output: guacamole_client_ecr_arn"
  type        = string
}

variable "foundation_guacamole_client_ecr_url" {
  description = "foundation remote-state output: guacamole_client_ecr_url"
  type        = string
}

variable "foundation_guacd_ecr_arn" {
  description = "foundation remote-state output: guacd_ecr_arn"
  type        = string
}

variable "foundation_guacd_ecr_url" {
  description = "foundation remote-state output: guacd_ecr_url"
  type        = string
}

variable "foundation_portal_ecr_arn" {
  description = "foundation remote-state output: portal_ecr_arn"
  type        = string
}

variable "foundation_portal_ecr_url" {
  description = "foundation remote-state output: portal_ecr_url"
  type        = string
}

variable "range_availability_zone" {
  description = "range remote-state output: availability_zone"
  type        = string
}

variable "range_engine_locks_table_arn" {
  description = "range remote-state output: engine_locks_table_arn"
  type        = string
}

variable "range_engine_locks_table_name" {
  description = "range remote-state output: engine_locks_table_name"
  type        = string
}

variable "range_engine_secrets_kms_key_alias" {
  description = "range remote-state output: engine_secrets_kms_key_alias"
  type        = string
}

variable "range_engine_secrets_kms_key_arn" {
  description = "range remote-state output: engine_secrets_kms_key_arn"
  type        = string
}

variable "range_engine_state_bucket_arn" {
  description = "range remote-state output: engine_state_bucket_arn"
  type        = string
}

variable "range_engine_state_bucket_name" {
  description = "range remote-state output: engine_state_bucket_name"
  type        = string
}

variable "range_firewall_endpoint_id" {
  description = "range remote-state output: firewall_endpoint_id"
  type        = string
}

variable "range_ngfw_data_security_group_id" {
  description = "range remote-state output: ngfw_data_security_group_id"
  type        = string
}

variable "range_ngfw_instance_profile_name" {
  description = "range remote-state output: ngfw_instance_profile_name"
  type        = string
}

variable "range_ngfw_instance_role_arn" {
  description = "range remote-state output: ngfw_instance_role_arn"
  type        = string
}

variable "range_ngfw_mgmt_security_group_id" {
  description = "range remote-state output: ngfw_mgmt_security_group_id"
  type        = string
}

variable "range_ngfw_subnet_cidr" {
  description = "range remote-state output: ngfw_subnet_cidr"
  type        = string
}

variable "range_ngfw_subnet_id" {
  description = "range remote-state output: ngfw_subnet_id"
  type        = string
}

variable "range_private_route_table_id" {
  description = "range remote-state output: private_route_table_id"
  type        = string
}

variable "range_provider_api_endpoint_security_group_id" {
  description = "range remote-state output: provider_api_endpoint_security_group_id"
  type        = string
}

variable "range_range_egress_mode" {
  description = "range remote-state output: range_egress_mode"
  type        = string
}

variable "range_range_instance_profile_arn" {
  description = "range remote-state output: range_instance_profile_arn"
  type        = string
}

variable "range_range_instance_profile_name" {
  description = "range remote-state output: range_instance_profile_name"
  type        = string
}

variable "range_range_instance_role_arn" {
  description = "range remote-state output: range_instance_role_arn"
  type        = string
}

variable "range_s3_endpoint_id" {
  description = "range remote-state output: s3_endpoint_id"
  type        = string
}

variable "range_ssm_endpoints_subnet_cidr" {
  description = "range remote-state output: ssm_endpoints_subnet_cidr"
  type        = string
}

variable "range_vm_series_ami_id" {
  description = "range remote-state output: vm_series_ami_id"
  type        = string
}

variable "range_vm_series_instance_type" {
  description = "range remote-state output: vm_series_instance_type"
  type        = string
}

variable "range_vpc_cidr" {
  description = "range remote-state output: vpc_cidr"
  type        = string
}

variable "range_vpc_id" {
  description = "range remote-state output: vpc_id"
  type        = string
}

variable "range_vpn_edge_subnet_id" {
  description = "range remote-state output: vpn_edge_subnet_id"
  type        = string
}
