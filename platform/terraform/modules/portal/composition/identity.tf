# Portal Composition - identity
#
# Cognito user pool.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# Cognito
# ------------------------------------------------------------------------------

module "cognito" {
  source = "../cognito"

  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  environment              = var.environment
  aws_region               = var.aws_region
  log_retention_days       = var.log_retention_days
  secrets_kms_key_arn      = aws_kms_key.secrets_manager.arn
  cognito_domain_prefix    = var.cognito_domain_prefix
  callback_urls            = ["https://${var.domain_name}/oidc/callback/"]
  logout_urls              = ["https://${var.domain_name}/"]
  allowed_email_domains    = var.allowed_email_domains
  allowed_emails           = var.allowed_emails

  # Client-secret rotation (#159): operator-triggered Lambda + scheduled email reminder.
  portal_asg_name          = module.ec2.asg_name
  enable_autoscaling       = var.enable_autoscaling
  alerts_topic_arn         = aws_sns_topic.alerts.arn
  enable_rotation_reminder = var.alarm_email != ""
  deletion_protection      = var.cognito_deletion_protection

  # Longer token validity means less frequent MFA prompts; environments that
  # want the tighter default pass 1.
  access_token_validity_hours = var.cognito_access_token_validity_hours
  id_token_validity_hours     = var.cognito_id_token_validity_hours

  tags = var.tags
}
