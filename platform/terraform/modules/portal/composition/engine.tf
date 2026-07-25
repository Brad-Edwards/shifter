# Portal Composition - engine
#
# Engine provisioner and Guacamole, plus their historical state moves.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# Note: SSH rules from Portal to Kali/Victim are defined in the range VPC module
# (terraform/modules/range/vpc/main.tf) using the portal_vpc_cidr variable.
# Do not duplicate them here.

# ------------------------------------------------------------------------------
# Engine Provisioner (ECS Fargate)
# Note: Defined before log_aggregation so its log groups can be included
# ------------------------------------------------------------------------------

module "engine_provisioner" {
  source = "../../engine-provisioner"

  name_prefix                 = local.name_prefix
  iam_name_prefix             = local.iam_name_prefix
  permissions_boundary_arn    = local.ci_role_permissions_boundary_arn
  environment                 = var.environment
  cloud_provider              = var.cloud_provider
  tags                        = var.tags
  log_retention_days          = var.log_retention_days
  secrets_manager_kms_key_arn = aws_kms_key.secrets_manager.arn

  # ECR
  ecr_repository_url     = var.foundation_engine_provisioner_ecr_url
  container_image_tag    = var.engine_container_tag
  container_image_digest = var.engine_container_image_digest

  # Networking (Portal VPC for RDS access)
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids

  # Database (direct RDS connection - hostname only, port passed separately)
  db_host        = module.rds.db_instance_address
  db_port        = 5432
  db_name        = var.db_name
  db_resource_id = module.rds.db_resource_id

  # RDS security group (for adding ingress rule)
  rds_security_group_id = module.rds.db_security_group_id

  # Engine state (from Range environment)
  engine_state_bucket          = var.range_engine_state_bucket_name
  engine_state_bucket_arn      = var.range_engine_state_bucket_arn
  engine_locks_table           = var.range_engine_locks_table_name
  engine_locks_table_arn       = var.range_engine_locks_table_arn
  engine_secrets_kms_key_arn   = var.range_engine_secrets_kms_key_arn
  engine_secrets_kms_key_alias = var.range_engine_secrets_kms_key_alias

  # Range VPC configuration
  range_vpc_id                = var.range_vpc_id
  range_vpc_cidr              = var.range_vpc_cidr
  range_route_table_id        = var.range_private_route_table_id
  range_availability_zone     = var.range_availability_zone
  range_instance_profile_arn  = var.range_range_instance_profile_arn
  range_instance_profile_name = var.range_range_instance_profile_name
  range_instance_role_arn     = var.range_range_instance_role_arn

  # AWS Polaris Bedrock agent credential profile (#1377); off unless populated
  # via the deploy-secrets tfvars for an environment that runs AWS Polaris. The
  # engine-provisioner module turns these into the AWS_POLARIS_AGENT_* task env
  # vars that config.load_aws_polaris_agent_config() consumes.
  aws_polaris_agent_region                       = var.aws_polaris_agent_region
  aws_polaris_agent_main_model_id                = var.aws_polaris_agent_main_model_id
  aws_polaris_agent_small_model_id               = var.aws_polaris_agent_small_model_id
  aws_polaris_agent_main_inference_profile_arn   = var.aws_polaris_agent_main_inference_profile_arn
  aws_polaris_agent_small_inference_profile_arn  = var.aws_polaris_agent_small_inference_profile_arn
  aws_polaris_agent_main_backing_model_arns      = var.aws_polaris_agent_main_backing_model_arns
  aws_polaris_agent_small_backing_model_arns     = var.aws_polaris_agent_small_backing_model_arns
  aws_polaris_agent_sts_session_duration_seconds = var.aws_polaris_agent_sts_session_duration_seconds
  aws_polaris_agent_refresh_window_seconds       = var.aws_polaris_agent_refresh_window_seconds

  # AMIs (from SSM Parameter Store)
  kali_ami_id    = data.aws_ssm_parameter.kali_ami.value
  victim_ami_id  = data.aws_ssm_parameter.victim_ami.value
  windows_ami_id = data.aws_ssm_parameter.windows_ami.value
  dc_ami_id      = data.aws_ssm_parameter.dc_ami.value

  # Prebaked DC configuration. dc_domain_password is sourced from
  # aws_secretsmanager_secret.dc_domain_password inside the
  # engine-provisioner module; no plaintext input from this stack.
  dc_domain_name = var.dc_domain_name

  # Instance types
  kali_instance_type   = var.kali_instance_type
  victim_instance_type = var.victim_instance_type

  # S3
  agent_s3_bucket          = module.s3.bucket_name
  agent_s3_bucket_arn      = module.s3.bucket_arn
  s3_endpoint_id           = try(var.range_s3_endpoint_id, "")
  firewall_endpoint_id     = var.range_firewall_endpoint_id != null ? var.range_firewall_endpoint_id : ""
  range_egress_mode        = try(var.range_range_egress_mode, "allowlist")
  range_vpn_edge_subnet_id = try(var.range_vpn_edge_subnet_id, "")
  range_vpn_provider_endpoint_security_group_id = try(
    var.range_provider_api_endpoint_security_group_id,
    "",
  )
  ssm_endpoints_subnet_cidr = try(var.range_ssm_endpoints_subnet_cidr, "")

  # Portal VPC configuration (for terminal SSH routing)
  portal_vpc_cidr       = module.vpc.vpc_cidr
  portal_vpc_peering_id = aws_vpc_peering_connection.portal_to_range.id

  # NGFW (VM-Series) - from Range VPC outputs
  ngfw_mgmt_security_group_id = var.range_ngfw_mgmt_security_group_id != null ? var.range_ngfw_mgmt_security_group_id : ""
  ngfw_data_security_group_id = var.range_ngfw_data_security_group_id != null ? var.range_ngfw_data_security_group_id : ""
  ngfw_ami_id                 = var.range_vm_series_ami_id
  ngfw_instance_type          = var.range_vm_series_instance_type
  ngfw_subnet_id              = var.range_ngfw_subnet_id != null ? var.range_ngfw_subnet_id : ""
  ngfw_subnet_cidr            = var.range_ngfw_subnet_cidr != null ? var.range_ngfw_subnet_cidr : ""
  ngfw_instance_profile_name  = var.range_ngfw_instance_profile_name != null ? var.range_ngfw_instance_profile_name : ""
  ngfw_instance_role_arn      = var.range_ngfw_instance_role_arn != null ? var.range_ngfw_instance_role_arn : ""

  # Messaging (SNS topic for range event publishing)
  sns_topic_arn   = module.messaging.sns_topic_arn
  sns_kms_key_arn = module.messaging.kms_key_arn

  depends_on = [module.vpc]

  # Alarms
  enable_alarms = var.engine_enable_alarms
  alarm_email   = var.engine_alarm_email
}

moved {
  from = module.pulumi_provisioner
  to   = module.engine_provisioner
}

# ------------------------------------------------------------------------------
# Guacamole (Remote Desktop Gateway)
# ------------------------------------------------------------------------------

module "guacamole" {
  source = "../../guacamole"

  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  environment              = var.environment
  tags                     = var.tags
  secrets_kms_key_arn      = aws_kms_key.secrets_manager.arn

  # Networking (Portal VPC)
  vpc_id                   = module.vpc.vpc_id
  private_subnet_ids       = module.vpc.private_subnet_ids
  range_vpc_cidr           = var.range_vpc_cidr
  portal_security_group_id = module.ec2.security_group_id
  enable_portal_sg_rule    = true

  # Shared ALB (from Portal ALB module)
  alb_listener_arn      = module.alb.https_listener_arn
  alb_security_group_id = module.alb.security_group_id

  # Drain in-flight RDP/SSH browser sessions on target removal (#931).
  target_deregistration_delay_seconds = var.guacamole_deregistration_delay_seconds

  # ECR (from foundation remote state)
  guacd_ecr_repository_url            = var.foundation_guacd_ecr_url
  guacd_ecr_repository_arn            = var.foundation_guacd_ecr_arn
  guacamole_client_ecr_repository_url = var.foundation_guacamole_client_ecr_url
  guacamole_client_ecr_repository_arn = var.foundation_guacamole_client_ecr_arn

  # Logging (shared with portal)
  log_retention_days = var.log_retention_days

  # Container configuration
  guacd_image_tag                = var.guacd_image_tag
  guacamole_client_image_tag     = var.guacamole_client_image_tag
  guacd_cpu                      = var.guacd_cpu
  guacd_memory                   = var.guacd_memory
  guacamole_client_cpu           = var.guacamole_client_cpu
  guacamole_client_memory        = var.guacamole_client_memory
  guacd_desired_count            = var.guacd_desired_count
  guacamole_client_desired_count = var.guacamole_client_desired_count

  # Database configuration
  db_instance_class        = var.guacamole_db_instance_class
  db_allocated_storage     = var.guacamole_db_allocated_storage
  db_max_allocated_storage = var.guacamole_db_max_allocated_storage
  db_engine_version        = var.guacamole_db_engine_version
  db_ca_cert_identifier    = var.guacamole_db_ca_cert_identifier
  db_multi_az              = var.guacamole_db_multi_az
  db_backup_retention_days = var.guacamole_db_backup_retention_days
  db_deletion_protection   = var.guacamole_db_deletion_protection
  db_skip_final_snapshot   = var.guacamole_db_skip_final_snapshot
  db_apply_immediately     = var.guacamole_db_apply_immediately

  # Autoscaling
  enable_autoscaling       = var.guacamole_enable_autoscaling
  autoscaling_min_capacity = var.guacamole_autoscaling_min_capacity
  autoscaling_max_capacity = var.guacamole_autoscaling_max_capacity
  autoscaling_cpu_target   = var.guacamole_autoscaling_cpu_target

  # Secrets
  secrets_recovery_window_days = var.guacamole_secrets_recovery_window_days

  # OIDC/Cognito authentication
  enable_oidc          = var.guacamole_enable_oidc
  cognito_user_pool_id = module.cognito.user_pool_id
  cognito_domain       = module.cognito.cognito_domain
  aws_region           = var.aws_region
  domain_name          = var.domain_name

  depends_on = [module.vpc]
}
