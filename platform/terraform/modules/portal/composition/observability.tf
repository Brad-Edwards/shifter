# Portal Composition - observability
#
# Log aggregation and Bedrock invocation logging.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# Log Aggregation (S3, SQS, Firehose for internal observability)
# Note: XDR CloudTrail integration is managed via CloudFormation, not Terraform
# ------------------------------------------------------------------------------

module "log_aggregation" {
  source = "../../log-aggregation"

  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  environment              = var.environment
  aws_region               = var.aws_region
  log_retention_days       = var.log_retention_days
  enable_log_aggregation   = var.enable_log_aggregation

  # Phase 5: ALB and WAF logging
  enable_alb_access_logs = var.enable_alb_access_logs
  enable_waf_logging     = var.enable_waf_logging

  # Log group sources (for CloudWatch subscription filters)
  source_log_group_names = var.enable_log_aggregation ? concat(
    [module.ec2.log_group_name],
    [module.cognito.log_group_name],
    # Phase 5: VPC flow logs and RDS logs
    var.enable_vpc_flow_logs ? [module.vpc.flow_logs_log_group_name] : [],
    var.enable_rds_log_exports ? module.rds.log_group_names : [],
    # Engine provisioner logs
    module.engine_provisioner.log_group_names,
    # Guacamole logs
    module.guacamole.log_group_names,
    # Portal east-west inspection (#122)
    var.enable_portal_inspection ? [module.vpc.firewall_log_group_name] : [],
  ) : []

  tags = var.tags

  # Monitoring
  enable_alarms = var.log_aggregation_enable_alarms
  alarm_email   = var.log_aggregation_alarm_email
}

# ------------------------------------------------------------------------------
# Bedrock Model Invocation Logging
# Captures invocation details including errors for debugging
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "bedrock" {
  count = var.enable_bedrock_logging ? 1 : 0

  name              = "/aws/bedrock/${local.name_prefix}-invocations"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-bedrock-invocations"
  })
}

resource "aws_iam_role" "bedrock_logging" {
  count = var.enable_bedrock_logging ? 1 : 0

  name = "${local.iam_name_prefix}-bedrock-logging"

  permissions_boundary = local.ci_role_permissions_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "bedrock.amazonaws.com"
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "bedrock_logging" {
  count = var.enable_bedrock_logging ? 1 : 0

  name = "cloudwatch-logs"
  role = aws_iam_role.bedrock_logging[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "${aws_cloudwatch_log_group.bedrock[0].arn}:*"
    }]
  })
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count = var.enable_bedrock_logging ? 1 : 0

  logging_config {
    embedding_data_delivery_enabled = false
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock[0].name
      role_arn       = aws_iam_role.bedrock_logging[0].arn
    }
  }
}
