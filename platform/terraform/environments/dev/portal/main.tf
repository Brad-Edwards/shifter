terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ------------------------------------------------------------------------------
# Remote State - Foundation (ECR)
# ------------------------------------------------------------------------------

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = var.terraform_state_bucket
    key    = "shifter/dev/terraform.tfstate"
    region = var.terraform_state_region
  }
}

# ------------------------------------------------------------------------------
# Remote State - Range VPC
# ------------------------------------------------------------------------------

data "terraform_remote_state" "range" {
  backend = "s3"
  config = {
    bucket = var.terraform_state_bucket
    key    = "dev/range/terraform.tfstate"
    region = var.terraform_state_region
  }
}

# ------------------------------------------------------------------------------
# AMI IDs from SSM Parameter Store
# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# Portal composition
# ------------------------------------------------------------------------------
# The shared dev/proof/prod resource graph. This root owns the backend,
# provider, lockfile, remote-state reads, variable contract and outputs;
# the module owns the resource graph (#688).

module "portal" {
  source = "../../../modules/portal/composition"

  aces_package_bucket_arn                        = var.aces_package_bucket_arn
  aces_package_prefix                            = var.aces_package_prefix
  alarm_email                                    = var.alarm_email
  alb_idle_timeout_seconds                       = var.alb_idle_timeout_seconds
  allowed_email_domains                          = var.allowed_email_domains
  allowed_emails                                 = var.allowed_emails
  app_port                                       = var.app_port
  asg_desired_capacity                           = var.asg_desired_capacity
  asg_max_size                                   = var.asg_max_size
  asg_min_size                                   = var.asg_min_size
  asg_warm_pool_min_size                         = var.asg_warm_pool_min_size
  asg_warm_pool_state                            = var.asg_warm_pool_state
  aws_polaris_agent_main_backing_model_arns      = var.aws_polaris_agent_main_backing_model_arns
  aws_polaris_agent_main_inference_profile_arn   = var.aws_polaris_agent_main_inference_profile_arn
  aws_polaris_agent_main_model_id                = var.aws_polaris_agent_main_model_id
  aws_polaris_agent_refresh_window_seconds       = var.aws_polaris_agent_refresh_window_seconds
  aws_polaris_agent_region                       = var.aws_polaris_agent_region
  aws_polaris_agent_small_backing_model_arns     = var.aws_polaris_agent_small_backing_model_arns
  aws_polaris_agent_small_inference_profile_arn  = var.aws_polaris_agent_small_inference_profile_arn
  aws_polaris_agent_small_model_id               = var.aws_polaris_agent_small_model_id
  aws_polaris_agent_sts_session_duration_seconds = var.aws_polaris_agent_sts_session_duration_seconds
  aws_region                                     = var.aws_region
  az_count                                       = var.az_count
  cloud_provider                                 = var.cloud_provider
  cognito_domain_prefix                          = var.cognito_domain_prefix
  ctf_from_email                                 = var.ctf_from_email
  ctfd_ami_id                                    = var.ctfd_ami_id
  ctfd_docker_buildx_version                     = var.ctfd_docker_buildx_version
  ctfd_docker_compose_version                    = var.ctfd_docker_compose_version
  ctfd_domain                                    = var.ctfd_domain
  ctfd_git_ref                                   = var.ctfd_git_ref
  ctfd_instance_type                             = var.ctfd_instance_type
  ctfd_repo_url                                  = var.ctfd_repo_url
  ctfd_root_volume_iops                          = var.ctfd_root_volume_iops
  ctfd_root_volume_size                          = var.ctfd_root_volume_size
  ctfd_root_volume_throughput                    = var.ctfd_root_volume_throughput
  ctfd_root_volume_type                          = var.ctfd_root_volume_type
  ctfd_ssh_allowed_cidrs                         = var.ctfd_ssh_allowed_cidrs
  ctfd_ssh_public_key                            = var.ctfd_ssh_public_key
  db_allocated_storage                           = var.db_allocated_storage
  db_apply_immediately                           = var.db_apply_immediately
  db_backup_retention_days                       = var.db_backup_retention_days
  db_ca_cert_identifier                          = var.db_ca_cert_identifier
  db_deletion_protection                         = var.db_deletion_protection
  db_engine_version                              = var.db_engine_version
  db_instance_class                              = var.db_instance_class
  db_max_allocated_storage                       = var.db_max_allocated_storage
  db_multi_az                                    = var.db_multi_az
  db_name                                        = var.db_name
  db_skip_final_snapshot                         = var.db_skip_final_snapshot
  db_username                                    = var.db_username
  dc_domain_name                                 = var.dc_domain_name
  docker_stop_timeout                            = var.docker_stop_timeout
  domain_name                                    = var.domain_name
  ec2_ami_id                                     = var.ec2_ami_id
  ec2_instance_type                              = var.ec2_instance_type
  ec2_root_volume_size                           = var.ec2_root_volume_size
  email_backend                                  = var.email_backend
  enable_alb_access_logs                         = var.enable_alb_access_logs
  enable_autoscaling                             = var.enable_autoscaling
  enable_bedrock_logging                         = var.enable_bedrock_logging
  enable_ctfd                                    = var.enable_ctfd
  enable_log_aggregation                         = var.enable_log_aggregation
  enable_nat_gateway                             = var.enable_nat_gateway
  enable_portal_capacity_alarms                  = var.enable_portal_capacity_alarms
  enable_portal_inspection                       = var.enable_portal_inspection
  enable_rds_log_exports                         = var.enable_rds_log_exports
  enable_redis                                   = var.enable_redis
  enable_vpc_flow_logs                           = var.enable_vpc_flow_logs
  enable_waf_logging                             = var.enable_waf_logging
  engine_container_image_digest                  = var.engine_container_image_digest
  engine_container_tag                           = var.engine_container_tag
  environment                                    = var.environment
  firewall_log_retention_days                    = var.firewall_log_retention_days
  guacamole_autoscaling_cpu_target               = var.guacamole_autoscaling_cpu_target
  guacamole_autoscaling_max_capacity             = var.guacamole_autoscaling_max_capacity
  guacamole_autoscaling_min_capacity             = var.guacamole_autoscaling_min_capacity
  guacamole_client_cpu                           = var.guacamole_client_cpu
  guacamole_client_desired_count                 = var.guacamole_client_desired_count
  guacamole_client_image_tag                     = var.guacamole_client_image_tag
  guacamole_client_memory                        = var.guacamole_client_memory
  guacamole_db_allocated_storage                 = var.guacamole_db_allocated_storage
  guacamole_db_apply_immediately                 = var.guacamole_db_apply_immediately
  guacamole_db_backup_retention_days             = var.guacamole_db_backup_retention_days
  guacamole_db_ca_cert_identifier                = var.guacamole_db_ca_cert_identifier
  guacamole_db_deletion_protection               = var.guacamole_db_deletion_protection
  guacamole_db_engine_version                    = var.guacamole_db_engine_version
  guacamole_db_instance_class                    = var.guacamole_db_instance_class
  guacamole_db_max_allocated_storage             = var.guacamole_db_max_allocated_storage
  guacamole_db_multi_az                          = var.guacamole_db_multi_az
  guacamole_db_skip_final_snapshot               = var.guacamole_db_skip_final_snapshot
  guacamole_deregistration_delay_seconds         = var.guacamole_deregistration_delay_seconds
  guacamole_enable_autoscaling                   = var.guacamole_enable_autoscaling
  guacamole_enable_oidc                          = var.guacamole_enable_oidc
  guacamole_secrets_recovery_window_days         = var.guacamole_secrets_recovery_window_days
  guacd_cpu                                      = var.guacd_cpu
  guacd_desired_count                            = var.guacd_desired_count
  guacd_image_tag                                = var.guacd_image_tag
  guacd_memory                                   = var.guacd_memory
  health_check_grace_period                      = var.health_check_grace_period
  health_check_path                              = var.health_check_path
  health_check_type                              = var.health_check_type
  instance_refresh_instance_warmup               = var.instance_refresh_instance_warmup
  instance_refresh_min_healthy_percentage        = var.instance_refresh_min_healthy_percentage
  kali_instance_type                             = var.kali_instance_type
  log_level                                      = var.log_level
  log_retention_days                             = var.log_retention_days
  messaging_alarm_dlq_threshold                  = var.messaging_alarm_dlq_threshold
  messaging_alarm_message_age_threshold          = var.messaging_alarm_message_age_threshold
  messaging_alarm_queue_depth_threshold          = var.messaging_alarm_queue_depth_threshold
  messaging_consumers                            = var.messaging_consumers
  messaging_dlq_max_receive_count                = var.messaging_dlq_max_receive_count
  messaging_dlq_message_retention_seconds        = var.messaging_dlq_message_retention_seconds
  messaging_enable_alarms                        = var.messaging_enable_alarms
  messaging_enable_dlq                           = var.messaging_enable_dlq
  messaging_message_retention_seconds            = var.messaging_message_retention_seconds
  messaging_visibility_timeout_seconds           = var.messaging_visibility_timeout_seconds
  portal_capacity_metrics_enabled                = var.portal_capacity_metrics_enabled
  portal_deregistration_delay_seconds            = var.portal_deregistration_delay_seconds
  portal_inspection_delete_protection            = var.portal_inspection_delete_protection
  portal_web_workers                             = var.portal_web_workers
  portal_worker_soft_concurrency                 = var.portal_worker_soft_concurrency
  redis_apply_immediately                        = var.redis_apply_immediately
  redis_enable_replication                       = var.redis_enable_replication
  redis_engine_version                           = var.redis_engine_version
  redis_node_type                                = var.redis_node_type
  scale_target_requests_per_target               = var.scale_target_requests_per_target
  scale_target_response_time_seconds             = var.scale_target_response_time_seconds
  scale_up_threshold                             = var.scale_up_threshold
  ses_domain                                     = var.ses_domain
  tags                                           = var.tags
  target_response_time_alarm_threshold_seconds   = var.target_response_time_alarm_threshold_seconds
  terminal_idle_timeout_seconds                  = var.terminal_idle_timeout_seconds
  terminal_max_session_seconds                   = var.terminal_max_session_seconds
  terminal_max_sessions                          = var.terminal_max_sessions
  terminal_max_sessions_per_user                 = var.terminal_max_sessions_per_user
  terminal_read_poll_seconds                     = var.terminal_read_poll_seconds
  termination_drain_timeout                      = var.termination_drain_timeout
  user_storage_bucket                            = var.user_storage_bucket
  victim_instance_type                           = var.victim_instance_type
  vpc_cidr                                       = var.vpc_cidr
  worker_busy_ratio_scale_out_threshold          = var.worker_busy_ratio_scale_out_threshold
  alb_enable_deletion_protection                 = false
  cognito_deletion_protection                    = false
  cognito_access_token_validity_hours            = 8
  cognito_id_token_validity_hours                = 8
  secret_recovery_window_in_days                 = 0
  engine_enable_alarms                           = false
  engine_alarm_email                             = ""
  log_aggregation_enable_alarms                  = false
  log_aggregation_alarm_email                    = ""

  # Remote-state values, mapped explicitly into typed inputs.
  foundation_engine_provisioner_ecr_url         = data.terraform_remote_state.foundation.outputs.engine_provisioner_ecr_url
  foundation_guacamole_client_ecr_arn           = data.terraform_remote_state.foundation.outputs.guacamole_client_ecr_arn
  foundation_guacamole_client_ecr_url           = data.terraform_remote_state.foundation.outputs.guacamole_client_ecr_url
  foundation_guacd_ecr_arn                      = data.terraform_remote_state.foundation.outputs.guacd_ecr_arn
  foundation_guacd_ecr_url                      = data.terraform_remote_state.foundation.outputs.guacd_ecr_url
  foundation_portal_ecr_arn                     = data.terraform_remote_state.foundation.outputs.portal_ecr_arn
  foundation_portal_ecr_url                     = data.terraform_remote_state.foundation.outputs.portal_ecr_url
  range_availability_zone                       = data.terraform_remote_state.range.outputs.availability_zone
  range_engine_locks_table_arn                  = data.terraform_remote_state.range.outputs.engine_locks_table_arn
  range_engine_locks_table_name                 = data.terraform_remote_state.range.outputs.engine_locks_table_name
  range_engine_secrets_kms_key_alias            = data.terraform_remote_state.range.outputs.engine_secrets_kms_key_alias
  range_engine_secrets_kms_key_arn              = data.terraform_remote_state.range.outputs.engine_secrets_kms_key_arn
  range_engine_state_bucket_arn                 = data.terraform_remote_state.range.outputs.engine_state_bucket_arn
  range_engine_state_bucket_name                = data.terraform_remote_state.range.outputs.engine_state_bucket_name
  range_firewall_endpoint_id                    = data.terraform_remote_state.range.outputs.firewall_endpoint_id
  range_ngfw_data_security_group_id             = data.terraform_remote_state.range.outputs.ngfw_data_security_group_id
  range_ngfw_instance_profile_name              = data.terraform_remote_state.range.outputs.ngfw_instance_profile_name
  range_ngfw_instance_role_arn                  = data.terraform_remote_state.range.outputs.ngfw_instance_role_arn
  range_ngfw_mgmt_security_group_id             = data.terraform_remote_state.range.outputs.ngfw_mgmt_security_group_id
  range_ngfw_subnet_cidr                        = data.terraform_remote_state.range.outputs.ngfw_subnet_cidr
  range_ngfw_subnet_id                          = data.terraform_remote_state.range.outputs.ngfw_subnet_id
  range_private_route_table_id                  = data.terraform_remote_state.range.outputs.private_route_table_id
  range_provider_api_endpoint_security_group_id = data.terraform_remote_state.range.outputs.provider_api_endpoint_security_group_id
  range_range_egress_mode                       = data.terraform_remote_state.range.outputs.range_egress_mode
  range_range_instance_profile_arn              = data.terraform_remote_state.range.outputs.range_instance_profile_arn
  range_range_instance_profile_name             = data.terraform_remote_state.range.outputs.range_instance_profile_name
  range_range_instance_role_arn                 = data.terraform_remote_state.range.outputs.range_instance_role_arn
  range_s3_endpoint_id                          = data.terraform_remote_state.range.outputs.s3_endpoint_id
  range_ssm_endpoints_subnet_cidr               = data.terraform_remote_state.range.outputs.ssm_endpoints_subnet_cidr
  range_vm_series_ami_id                        = data.terraform_remote_state.range.outputs.vm_series_ami_id
  range_vm_series_instance_type                 = data.terraform_remote_state.range.outputs.vm_series_instance_type
  range_vpc_cidr                                = data.terraform_remote_state.range.outputs.vpc_cidr
  range_vpc_id                                  = data.terraform_remote_state.range.outputs.vpc_id
  range_vpn_edge_subnet_id                      = data.terraform_remote_state.range.outputs.vpn_edge_subnet_id
}
