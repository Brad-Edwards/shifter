# Portal Composition - compute
#
# Parameter Store config, the portal EC2 fleet, and the optional CTFd host.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# SSM Deployment (Parameter Store + SSM Document)
# ------------------------------------------------------------------------------

module "ssm" {
  source = "../ssm"

  environment    = var.environment
  cloud_provider = var.cloud_provider
  name_prefix    = local.name_prefix
  aws_region     = var.aws_region
  tags           = var.tags

  # ECR configuration
  ecr_registry        = split("/", var.foundation_portal_ecr_url)[0]
  ecr_repository_name = split("/", var.foundation_portal_ecr_url)[1]

  # Secrets Manager ARNs
  db_secret_arn                 = module.rds.db_credentials_secret_arn
  app_secret_arn                = aws_secretsmanager_secret.app.arn
  cognito_secret_arn            = module.cognito.cognito_secret_arn
  guacamole_secret_arn          = module.guacamole.json_auth_secret_arn
  guacamole_base_url            = "https://${var.domain_name}/guacamole"
  guacamole_api_base_url        = module.guacamole.guacamole_client_internal_url
  dc_domain_password_secret_arn = module.engine_provisioner.dc_domain_password_secret_arn

  # Application configuration
  domain_name       = var.domain_name
  s3_bucket_name    = var.user_storage_bucket
  ctfd_platform_url = var.enable_ctfd ? "${module.ctfd[0].url}/login" : ""

  # Engine provisioner configuration
  engine_ecs_cluster_arn        = module.engine_provisioner.ecs_cluster_arn
  engine_task_definition_family = module.engine_provisioner.task_definition_family
  engine_ecs_security_group_id  = module.engine_provisioner.ecs_security_group_id
  engine_private_subnet_ids     = join(",", module.vpc.private_subnet_ids)

  # Messaging configuration
  sqs_cms_url    = module.messaging.sqs_queue_urls["cms"]
  sqs_engine_url = module.messaging.sqs_queue_urls["engine"]
  sqs_mc_url     = module.messaging.sqs_queue_urls["mc"]

  range_events_topic_id = module.messaging.sns_topic_arn
  # Redis wiring is environment-owned and decoupled from autoscaling (ADR-018, #849).
  redis_endpoint = var.enable_redis ? module.redis.redis_endpoint : ""
  enable_redis   = var.enable_redis
  # AUTH + in-transit encryption references (#938). Non-secret: the token stays
  # in Secrets Manager and is hydrated into REDIS_PASSWORD by entrypoint.sh.
  redis_secret_arn = module.redis.redis_secret_arn
  redis_tls        = module.redis.redis_tls_enabled
  redis_ca_mode    = "system"

  # Database endpoint (direct RDS connection - hostname only, not endpoint with port)
  db_host_override        = module.rds.db_instance_address
  enable_db_host_override = true

  # Logging level (DEBUG for dev, INFO for prod)
  log_level = var.log_level

  # Email configuration
  email_backend  = var.email_backend
  ctf_from_email = var.ctf_from_email

  # Portal runtime capacity tunables (#930). Worker count is sized to the
  # instance vCPU budget; terminal caps are process-local, so the per-instance
  # ceiling is portal_web_workers * terminal_max_sessions.
  portal_web_workers             = var.portal_web_workers
  terminal_max_sessions          = var.terminal_max_sessions
  terminal_max_sessions_per_user = var.terminal_max_sessions_per_user
  terminal_idle_timeout_seconds  = var.terminal_idle_timeout_seconds
  terminal_max_session_seconds   = var.terminal_max_session_seconds
  terminal_read_poll_seconds     = var.terminal_read_poll_seconds

  # Portal web capacity metrics (#940). Enable flag and busy-ratio denominator
  # are env-owned and hydrated by both first-boot user_data and SSM redeploy.
  portal_capacity_metrics_enabled = var.portal_capacity_metrics_enabled
  portal_worker_soft_concurrency  = var.portal_worker_soft_concurrency
}

# ------------------------------------------------------------------------------
# EC2
# ------------------------------------------------------------------------------

module "ec2" {
  source = "../ec2"

  # Worker-container health alarm (#953) notifies the shared alerts topic.
  worker_health_alarm_actions = var.alarm_email != "" ? [aws_sns_topic.alerts.arn] : []

  aws_region               = var.aws_region
  environment              = var.environment
  cloud_provider           = var.cloud_provider
  ec2_ami_id               = var.ec2_ami_id
  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  vpc_id                   = module.vpc.vpc_id
  subnet_id                = module.vpc.private_subnet_ids[0]
  alb_security_group_id    = module.alb.security_group_id
  instance_type            = var.ec2_instance_type
  ecr_repository_arn       = var.foundation_portal_ecr_arn
  ecr_repository_url       = var.foundation_portal_ecr_url
  # The Redis AUTH token secret (#938) is included only on the in-transit-
  # encryption path; the single-node path returns "" and must not reach the IAM
  # Resource list (an empty ARN is invalid).
  secret_arns = concat(
    [
      module.rds.db_credentials_secret_arn,
      aws_secretsmanager_secret.app.arn,
      module.cognito.cognito_secret_arn,
      module.guacamole.json_auth_secret_arn,
      module.engine_provisioner.dc_domain_password_secret_arn,
    ],
    module.redis.redis_secret_arn != "" ? [module.redis.redis_secret_arn] : [],
  )
  secrets_manager_kms_key_arn = aws_kms_key.secrets_manager.arn
  db_resource_id              = module.rds.db_resource_id
  s3_bucket_arn               = module.s3.bucket_arn
  aces_package_bucket_arn     = var.aces_package_bucket_arn
  aces_package_prefix         = var.aces_package_prefix
  app_port                    = var.app_port
  root_volume_size            = var.ec2_root_volume_size

  # ECS permissions for engine provisioner
  ecs_cluster_arn            = module.engine_provisioner.ecs_cluster_arn
  ecs_task_definition_family = module.engine_provisioner.task_definition_family
  ecs_task_role_arn          = module.engine_provisioner.ecs_task_role_arn
  ecs_execution_role_arn     = module.engine_provisioner.ecs_execution_role_arn

  # Autoscaling configuration
  enable_autoscaling      = var.enable_autoscaling
  subnet_ids              = module.vpc.private_subnet_ids
  target_group_arn        = module.alb.target_group_arn
  alb_arn_suffix          = module.alb.alb_arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  asg_min_size            = var.asg_min_size
  asg_max_size            = var.asg_max_size
  asg_desired_capacity    = var.asg_desired_capacity
  asg_warm_pool_min_size  = var.asg_warm_pool_min_size
  asg_warm_pool_state     = var.asg_warm_pool_state

  # App-saturation autoscaling + observability (#940). Scale-out tracks ALB
  # request-path saturation, not average EC2 CPU; alarms/dashboard notify the
  # shared alerts topic.
  scale_target_requests_per_target             = var.scale_target_requests_per_target
  scale_target_response_time_seconds           = var.scale_target_response_time_seconds
  worker_busy_ratio_scale_out_threshold        = var.worker_busy_ratio_scale_out_threshold
  target_response_time_alarm_threshold_seconds = var.target_response_time_alarm_threshold_seconds
  enable_portal_capacity_alarms                = var.enable_portal_capacity_alarms
  portal_capacity_alarm_actions                = var.alarm_email != "" ? [aws_sns_topic.alerts.arn] : []

  # Connection-lifecycle drain (#931): bounded termination drain + graceful
  # container stop, sized below the ALB idle timeout / target drain.
  termination_drain_timeout               = var.termination_drain_timeout
  docker_stop_timeout                     = var.docker_stop_timeout
  instance_refresh_min_healthy_percentage = var.instance_refresh_min_healthy_percentage
  health_check_type                       = var.health_check_type
  health_check_grace_period               = var.health_check_grace_period
  instance_refresh_instance_warmup        = var.instance_refresh_instance_warmup

  redis_endpoint     = var.enable_redis ? module.redis.redis_endpoint : ""
  scale_up_threshold = var.scale_up_threshold
  log_retention_days = var.log_retention_days

  # Messaging (SQS queues for message consumers)
  sqs_queue_arns         = values(module.messaging.sqs_queue_arns)
  sqs_queue_urls         = module.messaging.sqs_queue_urls
  sqs_kms_key_arn        = module.messaging.kms_key_arn
  range_events_topic_arn = module.messaging.sns_topic_arn
  s3_kms_key_arn         = aws_kms_key.portal_s3.arn

  # Parameter Store prefix for user_data bootstrap
  ssm_parameter_store_prefix = module.ssm.parameter_store_prefix

  # SES
  ses_domain_identity_arn = module.ses.domain_identity_arn
  enable_ses              = true

  tags = var.tags

  # First boot installs Docker and configures ECR/SSM-backed deployment. Make
  # the portal AWS service endpoints part of the VPC dependency boundary so a
  # fresh account does not race private AWS API reachability.
  depends_on = [module.vpc]
}

# ------------------------------------------------------------------------------
# CTFd
# ------------------------------------------------------------------------------

module "ctfd" {
  count = var.enable_ctfd ? 1 : 0

  source = "../ctfd"

  aws_region               = var.aws_region
  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  vpc_id                   = module.vpc.vpc_id
  # Public-workload tier, kept out of the ALB ingress CIDR so CTFd cannot
  # reach Django:8000 / Guacamole:8080 directly (#911 NET-2 / #933).
  subnet_id = module.vpc.public_workload_subnet_ids[0]

  ami_id                 = var.ctfd_ami_id
  instance_type          = var.ctfd_instance_type
  root_volume_size       = var.ctfd_root_volume_size
  root_volume_type       = var.ctfd_root_volume_type
  root_volume_iops       = var.ctfd_root_volume_iops
  root_volume_throughput = var.ctfd_root_volume_throughput

  domain                 = var.ctfd_domain
  ctfd_repo_url          = var.ctfd_repo_url
  ctfd_git_ref           = var.ctfd_git_ref
  docker_compose_version = var.ctfd_docker_compose_version
  docker_buildx_version  = var.ctfd_docker_buildx_version
  ssh_public_key         = var.ctfd_ssh_public_key
  ssh_allowed_cidrs      = var.ctfd_ssh_allowed_cidrs

  tags = var.tags
}
