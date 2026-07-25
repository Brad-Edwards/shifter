# State address migration for the portal composition extraction (#688).
#
# Every resource and module call below moved from this root into
# module.portal. Terraform treats each as a rename rather than a
# destroy/create, so no infrastructure is replaced.
#
# The historical module.pulumi_provisioner -> module.engine_provisioner
# move is preserved and extended rather than deleted: Terraform follows
# the chain, so a state that has not yet crossed the first hop still
# lands on the final address.

moved {
  from = module.pulumi_provisioner
  to   = module.engine_provisioner
}

moved {
  from = module.vpc
  to   = module.portal.module.vpc
}

moved {
  from = module.rds
  to   = module.portal.module.rds
}

moved {
  from = module.alb
  to   = module.portal.module.alb
}

moved {
  from = module.redis
  to   = module.portal.module.redis
}

moved {
  from = module.cognito
  to   = module.portal.module.cognito
}

moved {
  from = module.backup_alerts
  to   = module.portal.module.backup_alerts
}

moved {
  from = module.messaging
  to   = module.portal.module.messaging
}

moved {
  from = module.ssm
  to   = module.portal.module.ssm
}

moved {
  from = module.ec2
  to   = module.portal.module.ec2
}

moved {
  from = module.ctfd
  to   = module.portal.module.ctfd
}

moved {
  from = module.s3
  to   = module.portal.module.s3
}

moved {
  from = module.engine_provisioner
  to   = module.portal.module.engine_provisioner
}

moved {
  from = module.guacamole
  to   = module.portal.module.guacamole
}

moved {
  from = module.ses
  to   = module.portal.module.ses
}

moved {
  from = module.log_aggregation
  to   = module.portal.module.log_aggregation
}

moved {
  from = aws_kms_key.secrets_manager
  to   = module.portal.aws_kms_key.secrets_manager
}

moved {
  from = aws_kms_alias.secrets_manager
  to   = module.portal.aws_kms_alias.secrets_manager
}

moved {
  from = aws_kms_key.portal_s3
  to   = module.portal.aws_kms_key.portal_s3
}

moved {
  from = aws_kms_alias.portal_s3
  to   = module.portal.aws_kms_alias.portal_s3
}

moved {
  from = aws_kms_key.redis_at_rest
  to   = module.portal.aws_kms_key.redis_at_rest
}

moved {
  from = aws_kms_alias.redis_at_rest
  to   = module.portal.aws_kms_alias.redis_at_rest
}

moved {
  from = aws_sns_topic.alerts
  to   = module.portal.aws_sns_topic.alerts
}

moved {
  from = aws_sns_topic_subscription.alerts_email
  to   = module.portal.aws_sns_topic_subscription.alerts_email
}

moved {
  from = aws_lb_target_group_attachment.portal
  to   = module.portal.aws_lb_target_group_attachment.portal
}

moved {
  from = aws_iam_role_policy.range_instance_portal_s3_kms_read
  to   = module.portal.aws_iam_role_policy.range_instance_portal_s3_kms_read
}

moved {
  from = random_password.django_secret_key
  to   = module.portal.random_password.django_secret_key
}

moved {
  from = random_id.field_encryption_key
  to   = module.portal.random_id.field_encryption_key
}

moved {
  from = aws_secretsmanager_secret.app
  to   = module.portal.aws_secretsmanager_secret.app
}

moved {
  from = aws_secretsmanager_secret_version.app
  to   = module.portal.aws_secretsmanager_secret_version.app
}

moved {
  from = aws_vpc_peering_connection.portal_to_range
  to   = module.portal.aws_vpc_peering_connection.portal_to_range
}

moved {
  from = aws_route.portal_to_range
  to   = module.portal.aws_route.portal_to_range
}

moved {
  from = aws_route.range_to_portal
  to   = module.portal.aws_route.range_to_portal
}

moved {
  from = aws_security_group_rule.portal_app_from_alb_subnets
  to   = module.portal.aws_security_group_rule.portal_app_from_alb_subnets
}

moved {
  from = aws_security_group_rule.guacamole_client_from_alb_subnets
  to   = module.portal.aws_security_group_rule.guacamole_client_from_alb_subnets
}

moved {
  from = aws_cloudwatch_log_group.bedrock
  to   = module.portal.aws_cloudwatch_log_group.bedrock
}

moved {
  from = aws_iam_role.bedrock_logging
  to   = module.portal.aws_iam_role.bedrock_logging
}

moved {
  from = aws_iam_role_policy.bedrock_logging
  to   = module.portal.aws_iam_role_policy.bedrock_logging
}

moved {
  from = aws_bedrock_model_invocation_logging_configuration.this
  to   = module.portal.aws_bedrock_model_invocation_logging_configuration.this
}

moved {
  from = aws_kms_key.cloudwatch_logs
  to   = module.portal.aws_kms_key.cloudwatch_logs
}

moved {
  from = aws_kms_alias.cloudwatch_logs
  to   = module.portal.aws_kms_alias.cloudwatch_logs
}
