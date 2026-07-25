# Portal Composition - messaging
#
# SNS alerting, backup alerts, SQS messaging, and SES.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# Shared Alerting SNS Topic
# ------------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${local.name_prefix}-alerts"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ------------------------------------------------------------------------------
# Backup-Failure Alerting (#160)
# ------------------------------------------------------------------------------
# RDS reports backup/snapshot failures as RDS events, delivered through an RDS
# event subscription to a CMK-encrypted SNS topic. This cannot reuse the shared
# `aws_sns_topic.alerts` above (AWS-managed key cannot grant the RDS service
# principal), so the module owns a dedicated CMK + topic. See
# docs/ops/disaster-recovery.md.

module "backup_alerts" {
  source = "../backup-alerts"

  name_prefix = local.name_prefix
  environment = var.environment
  alarm_email = var.alarm_email

  db_instance_identifiers = compact([
    module.rds.db_instance_id,
    module.guacamole.db_instance_id,
  ])

  tags = var.tags
}

# ------------------------------------------------------------------------------
# Messaging (SNS/SQS)
# ------------------------------------------------------------------------------

module "messaging" {
  source = "../messaging"

  name_prefix                = local.name_prefix
  tags                       = var.tags
  consumers                  = var.messaging_consumers
  visibility_timeout_seconds = var.messaging_visibility_timeout_seconds
  message_retention_seconds  = var.messaging_message_retention_seconds

  # Dead Letter Queue
  enable_dlq                    = var.messaging_enable_dlq
  dlq_max_receive_count         = var.messaging_dlq_max_receive_count
  dlq_message_retention_seconds = var.messaging_dlq_message_retention_seconds

  # CloudWatch Alarms
  enable_alarms               = var.messaging_enable_alarms
  alarm_queue_depth_threshold = var.messaging_alarm_queue_depth_threshold
  alarm_message_age_threshold = var.messaging_alarm_message_age_threshold
  alarm_dlq_threshold         = var.messaging_alarm_dlq_threshold
  alarm_actions               = var.alarm_email != "" ? [aws_sns_topic.alerts.arn] : []
}

# ------------------------------------------------------------------------------
# SES (Transactional Email)
# ------------------------------------------------------------------------------

module "ses" {
  source = "../ses"

  domain = var.ses_domain
}
