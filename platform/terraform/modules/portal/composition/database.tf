# Portal Composition - database
#
# RDS and Redis.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# RDS PostgreSQL
# ------------------------------------------------------------------------------

module "rds" {
  source = "../rds"

  name_prefix                = local.name_prefix
  iam_name_prefix            = local.iam_name_prefix
  permissions_boundary_arn   = local.ci_role_permissions_boundary_arn
  secrets_kms_key_arn        = aws_kms_key.secrets_manager.arn
  vpc_id                     = module.vpc.vpc_id
  subnet_ids                 = module.vpc.private_subnet_ids
  allowed_security_group_ids = [module.ec2.security_group_id]

  db_name               = var.db_name
  db_username           = var.db_username
  engine_version        = var.db_engine_version
  ca_cert_identifier    = var.db_ca_cert_identifier
  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  multi_az              = var.db_multi_az
  backup_retention_days = var.db_backup_retention_days
  deletion_protection   = var.db_deletion_protection
  skip_final_snapshot   = var.db_skip_final_snapshot
  apply_immediately     = var.db_apply_immediately

  # Phase 5: RDS Log Exports
  enable_log_exports = var.enable_rds_log_exports
  log_retention_days = var.log_retention_days

  tags = var.tags
}

# ------------------------------------------------------------------------------
# Redis (for Django Channels in ASG mode)
# ------------------------------------------------------------------------------

module "redis" {
  source = "../redis"

  name_prefix                = local.name_prefix
  iam_name_prefix            = local.iam_name_prefix
  vpc_id                     = module.vpc.vpc_id
  subnet_ids                 = module.vpc.private_subnet_ids
  allowed_security_group_ids = [module.ec2.security_group_id]
  node_type                  = var.redis_node_type
  engine_version             = var.redis_engine_version
  enable_replication         = var.redis_enable_replication
  apply_immediately          = var.redis_apply_immediately

  # AUTH + in-transit encryption (#938): the AUTH token secret is encrypted by
  # the portal CMK. is_active_channel_backend rejects a live channel layer on
  # the plaintext single-node path. redis_at_rest_kms_key_arn is the dedicated
  # data-at-rest CMK for the replication group (#1059).
  secrets_kms_key_arn         = aws_kms_key.secrets_manager.arn
  redis_at_rest_kms_key_arn   = aws_kms_key.redis_at_rest.arn
  cloudwatch_logs_kms_key_arn = aws_kms_key.cloudwatch_logs.arn
  permissions_boundary_arn    = local.ci_role_permissions_boundary_arn
  is_active_channel_backend   = var.enable_redis

  # Automatic Redis AUTH rotation (#159): only where the portal runs on a
  # refreshable ASG, so the rotation Lambda can roll consumers to the new token.
  enable_auth_rotation = var.enable_autoscaling
  portal_asg_name      = module.ec2.asg_name

  # CloudWatch Alarms
  enable_alarms = var.alarm_email != ""
  alarm_actions = var.alarm_email != "" ? [aws_sns_topic.alerts.arn] : []

  tags = var.tags
}
