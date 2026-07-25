# GitHub OIDC - Management Category Policy (#254)

# Management: SSM, Cognito, CloudWatch (Logs + Alarms), SNS, EventBridge
# checkov:skip=CKV_AWS_355:CI/CD requires broad SSM/Cognito/observability permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad SSM/Cognito/observability permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad SSM/Cognito/observability permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad SSM/Cognito/observability permissions. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "management" {
  # checkov:skip=CKV_AWS_287:CI/CD requires broad SSM/Cognito/observability permissions. Risk accepted, see #44
  name = "shifter-${var.environment}-management"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMRunCommand"
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation",
          "ssm:ListCommandInvocations",
          "ssm:DescribeInstanceInformation",
          # DescribeParameters is a list action that does not support
          # resource-level scoping; it must be granted on "*".
          "ssm:DescribeParameters"
        ]
        Resource = "*"
      },
      {
        Sid    = "SSMParameterStore"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:DeleteParameter",
          "ssm:DescribeParameters",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource",
          "ssm:ListTagsForResource"
        ]
        Resource = [
          # All shifter-namespaced parameters: range DC config, AMI IDs, and
          # per-environment portal parameters (/shifter/<env>/portal/*).
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/shifter/*"
        ]
      },
      {
        Sid    = "SSMPublicServiceParametersRead"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        # AWS-owned PUBLIC parameters (no account in the ARN) used to resolve
        # current base AMIs at build time - e.g. the Canonical Ubuntu and
        # Amazon Linux AMI-ID parameters the scenario bakes (techvault /
        # polaris golden ranges) read. Read-only; scoped to /aws/service/*.
        Resource = [
          "arn:aws:ssm:${var.aws_region}::parameter/aws/service/*"
        ]
      },
      {
        Sid      = "Cognito"
        Effect   = "Allow"
        Action   = ["cognito-idp:*"]
        Resource = "*"
      },
      {
        # CloudWatch Logs (log groups, streams, metric/subscription filters) for
        # the portal/range/firehose log pipelines. Action set is scoped to the
        # lifecycle Terraform exercises for these resources; log data is
        # operational, not secret. Describe/List/Put actions do not support
        # resource-level constraints, so Resource stays "*".
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy",
          "logs:CreateLogStream",
          "logs:DeleteLogStream",
          "logs:DescribeLogStreams",
          "logs:PutMetricFilter",
          "logs:DeleteMetricFilter",
          "logs:DescribeMetricFilters",
          "logs:PutSubscriptionFilter",
          "logs:DeleteSubscriptionFilter",
          "logs:DescribeSubscriptionFilters",
          "logs:AssociateKmsKey",
          "logs:DisassociateKmsKey",
          "logs:ListTagsForResource",
          "logs:TagResource",
          "logs:UntagResource",
          "logs:PutResourcePolicy",
          "logs:DeleteResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:ListLogDeliveries",
          "logs:GetLogDelivery",
          "logs:CreateLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery"
        ]
        Resource = "*"
      },
      {
        # CloudWatch metric alarms for portal/range. PutMetricAlarm and
        # DeleteAlarms accept a resource ARN, but DescribeAlarms does not, so the
        # statement keeps Resource "*" and scopes by action instead.
        Sid    = "CloudWatchAlarms"
        Effect = "Allow"
        Action = [
          "cloudwatch:DescribeAlarms",
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:ListTagsForResource",
          "cloudwatch:TagResource",
          "cloudwatch:UntagResource",
          # CloudWatch dashboards (portal capacity dashboard, modules/portal/ec2
          # observability.tf). Dashboard APIs do not support resource-level
          # scoping, so they share the statement's Resource "*".
          "cloudwatch:PutDashboard",
          "cloudwatch:GetDashboard",
          "cloudwatch:DeleteDashboards",
          "cloudwatch:ListDashboards"
        ]
        Resource = "*"
      },
      {
        Sid    = "SNS"
        Effect = "Allow"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
          "sns:GetTopicAttributes",
          "sns:SetTopicAttributes",
          "sns:ListTagsForResource",
          "sns:TagResource",
          "sns:UntagResource",
          "sns:Subscribe",
          "sns:Unsubscribe",
          "sns:GetSubscriptionAttributes",
          # RDS CreateEventSubscription runs a connectivity test-publish to the
          # target topic authorized with the *caller's* identity, so the deploy
          # role must hold sns:Publish on the managed topics or the backup-alerts
          # event subscription create fails with SNSNoAuthorization.
          "sns:Publish"
        ]
        Resource = [
          "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-portal-*",
          "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-range-*"
        ]
      },
      {
        Sid    = "SQS"
        Effect = "Allow"
        Action = ["sqs:*"]
        Resource = [
          "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-portal-*",
          "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-range-*"
        ]
      },
      {
        # SES domain identity + DKIM for portal transactional email. Identity
        # actions are not ARN-addressable, so the statement scopes by action and
        # keeps Resource "*".
        Sid    = "SES"
        Effect = "Allow"
        Action = [
          "ses:VerifyDomainIdentity",
          "ses:VerifyDomainDkim",
          "ses:DeleteIdentity",
          "ses:GetIdentityVerificationAttributes",
          "ses:GetIdentityDkimAttributes",
          "ses:SetIdentityDkimEnabled",
          "ses:GetIdentityMailFromDomainAttributes",
          "ses:GetIdentityNotificationAttributes",
          "ses:ListIdentities"
        ]
        Resource = "*"
      },
      {
        # Kinesis Firehose delivery streams for WAF / portal log pipelines.
        Sid    = "Firehose"
        Effect = "Allow"
        Action = ["firehose:*"]
        Resource = [
          "arn:aws:firehose:${var.aws_region}:${data.aws_caller_identity.current.account_id}:deliverystream/*-portal-*",
          "arn:aws:firehose:${var.aws_region}:${data.aws_caller_identity.current.account_id}:deliverystream/aws-waf-logs-*"
        ]
      },
      {
        Sid    = "EventBridge"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:DescribeRule",
          "events:EnableRule",
          "events:DisableRule",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:ListTargetsByRule",
          "events:ListTagsForResource",
          "events:TagResource",
          "events:UntagResource"
        ]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/*-portal-*"
      },
      {
        Sid    = "Budgets"
        Effect = "Allow"
        Action = [
          "budgets:ViewBudget",
          "budgets:ModifyBudget",
          "budgets:ListTagsForResource",
          "budgets:TagResource",
          "budgets:UntagResource"
        ]
        Resource = "arn:aws:budgets::${data.aws_caller_identity.current.account_id}:budget/shifter-*"
      }
    ]
  })
}
