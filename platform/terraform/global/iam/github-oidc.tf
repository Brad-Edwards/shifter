variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Environment name (dev or prod)"
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be 'dev' or 'prod'."
  }
}

variable "github_org" {
  description = "GitHub organization"
  type        = string
  default     = "Brad-Edwards"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "shifter"
}

# Get current AWS account ID
data "aws_caller_identity" "current" {}

# GitHub OIDC Provider
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1b511abead59c6ce207077c0bf0e0043b1382612"]

  tags = {
    Name    = "github-actions-oidc"
    Project = "shifter"
  }
}

# IAM Role for GitHub Actions
resource "aws_iam_role" "github_actions" {
  name = "github-actions-shifter-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${var.github_repo}:*"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "github-actions-shifter-${var.environment}"
    Project     = "shifter"
    Environment = var.environment
  }
}

# ------------------------------------------------------------------------------
# Managed IAM Policies
#
# Consolidated by AWS service category (#254) to stay under AWS's hard limit of
# 10 managed policies per role. Five domain policies (compute, networking, data,
# security, management) leave headroom for future growth: a new service should
# extend an existing category, not add an eleventh attachment. The
# `check_tf_iam_role_naming` gate enforces the attachment cap. Consolidation is a
# structural move of existing statements; no permissions are broadened.
# ------------------------------------------------------------------------------

# Permissions boundary applied to every CI-created shifter-* role (#253).
# Standalone policy referenced by the security policy's iam:CreateRole condition;
# intentionally NOT attached to the github_actions role.
resource "aws_iam_policy" "ci_role_permissions_boundary" {
  name = "shifter-${var.environment}-ci-role-boundary"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyIamEscalation"
        Effect   = "Deny"
        Action   = "iam:*"
        Resource = "*"
      }
    ]
  })
}

# Compute: EC2, Auto Scaling, Lambda, ECS
# checkov:skip=CKV_AWS_355:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "compute" {
  # checkov:skip=CKV_AWS_287:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
  name = "shifter-${var.environment}-compute"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # TODO: Scope down EC2 permissions - see GitHub issue for audit
      {
        Sid      = "EC2"
        Effect   = "Allow"
        Action   = ["ec2:*"]
        Resource = "*"
      },
      {
        Sid    = "AutoScaling"
        Effect = "Allow"
        Action = [
          "autoscaling:CreateAutoScalingGroup",
          "autoscaling:DeleteAutoScalingGroup",
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:UpdateAutoScalingGroup",
          "autoscaling:CreateLaunchConfiguration",
          "autoscaling:DeleteLaunchConfiguration",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:CreateOrUpdateTags",
          "autoscaling:DeleteTags",
          "autoscaling:DescribeTags",
          "autoscaling:PutScalingPolicy",
          "autoscaling:DeletePolicy",
          "autoscaling:DescribePolicies",
          "autoscaling:SetDesiredCapacity",
          "autoscaling:TerminateInstanceInAutoScalingGroup",
          "autoscaling:StartInstanceRefresh",
          "autoscaling:DescribeInstanceRefreshes",
          "autoscaling:DescribeScalingActivities"
        ]
        Resource = "*"
      },
      {
        Sid    = "Lambda"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:GetFunctionCodeSigningConfig",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:ListVersionsByFunction",
          "lambda:PublishVersion",
          "lambda:AddPermission",
          "lambda:RemovePermission",
          "lambda:GetPolicy",
          "lambda:TagResource",
          "lambda:UntagResource",
          "lambda:ListTags"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:*"
      },
      {
        Sid    = "LambdaLayers"
        Effect = "Allow"
        Action = [
          "lambda:PublishLayerVersion",
          "lambda:GetLayerVersion",
          "lambda:DeleteLayerVersion",
          "lambda:ListLayerVersions"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:layer:*"
      },
      {
        Sid    = "ECS"
        Effect = "Allow"
        Action = [
          "ecs:*"
        ]
        Resource = "*"
      }
    ]
  })
}

# Networking: VPC, ELB, ACM, WAFv2, Network Firewall
# checkov:skip=CKV_AWS_355:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "networking" {
  name = "shifter-${var.environment}-networking"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VPC"
        Effect = "Allow"
        Action = [
          "ec2:*Vpc*",
          "ec2:*Subnet*",
          "ec2:*RouteTable*",
          "ec2:*Route",
          "ec2:*InternetGateway*",
          "ec2:*NatGateway*",
          "ec2:*Address*",
          "ec2:*SecurityGroup*",
          "ec2:*Tags",
          "ec2:Describe*",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:CreateFlowLogs",
          "ec2:DeleteFlowLogs",
          "ec2:DescribeFlowLogs"
        ]
        Resource = "*"
      },
      {
        Sid      = "ELB"
        Effect   = "Allow"
        Action   = ["elasticloadbalancing:*"]
        Resource = "*"
      },
      {
        Sid      = "ACM"
        Effect   = "Allow"
        Action   = ["acm:*"]
        Resource = "*"
      },
      {
        Sid    = "WAFv2"
        Effect = "Allow"
        Action = [
          "wafv2:CreateWebACL",
          "wafv2:DeleteWebACL",
          "wafv2:GetWebACL",
          "wafv2:UpdateWebACL",
          "wafv2:ListWebACLs",
          "wafv2:AssociateWebACL",
          "wafv2:DisassociateWebACL",
          "wafv2:GetWebACLForResource",
          "wafv2:ListResourcesForWebACL",
          "wafv2:ListTagsForResource",
          "wafv2:TagResource",
          "wafv2:UntagResource",
          "wafv2:DescribeManagedRuleGroup",
          "wafv2:ListAvailableManagedRuleGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "NetworkFirewall"
        Effect = "Allow"
        Action = [
          "network-firewall:CreateFirewall",
          "network-firewall:DeleteFirewall",
          "network-firewall:DescribeFirewall",
          "network-firewall:UpdateFirewallDeleteProtection",
          "network-firewall:UpdateFirewallDescription",
          "network-firewall:UpdateFirewallPolicy",
          "network-firewall:UpdateFirewallPolicyChangeProtection",
          "network-firewall:UpdateSubnetChangeProtection",
          "network-firewall:AssociateFirewallPolicy",
          "network-firewall:DisassociateSubnets",
          "network-firewall:AssociateSubnets",
          "network-firewall:CreateFirewallPolicy",
          "network-firewall:DeleteFirewallPolicy",
          "network-firewall:DescribeFirewallPolicy",
          "network-firewall:UpdateFirewallPolicy",
          "network-firewall:CreateRuleGroup",
          "network-firewall:DeleteRuleGroup",
          "network-firewall:DescribeRuleGroup",
          "network-firewall:UpdateRuleGroup",
          "network-firewall:ListFirewalls",
          "network-firewall:ListFirewallPolicies",
          "network-firewall:ListRuleGroups",
          "network-firewall:TagResource",
          "network-firewall:UntagResource",
          "network-firewall:ListTagsForResource",
          "network-firewall:DescribeLoggingConfiguration",
          "network-firewall:UpdateLoggingConfiguration"
        ]
        Resource = "*"
      }
    ]
  })
}

# Data: ECR, S3, DynamoDB, Pulumi state, RDS, ElastiCache
# checkov:skip=CKV_AWS_355:CI/CD requires broad data-store permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad data-store permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad data-store permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad data-store permissions for infrastructure management. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "data" {
  name = "shifter-${var.environment}-data"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECR"
        Effect   = "Allow"
        Action   = ["ecr:*"]
        Resource = "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/shifter-*"
      },
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "S3State"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = [
          "arn:aws:s3:::shifter-infra-*",
          "arn:aws:s3:::shifter-infra-*/*"
        ]
      },
      {
        Sid      = "S3UserStorage"
        Effect   = "Allow"
        Action   = ["s3:*"]
        Resource = "arn:aws:s3:::shifter-user-storage-*"
      },
      {
        Sid    = "DynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem"
        ]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/shifter-terraform-*"
      },
      {
        Sid    = "PulumiStateS3"
        Effect = "Allow"
        Action = [
          "s3:*"
        ]
        Resource = [
          "arn:aws:s3:::*-range-pulumi-state",
          "arn:aws:s3:::*-range-pulumi-state/*"
        ]
      },
      {
        Sid    = "PulumiStateDynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:CreateTable",
          "dynamodb:DeleteTable",
          "dynamodb:DescribeTable",
          "dynamodb:UpdateTable",
          "dynamodb:DescribeTimeToLive",
          "dynamodb:UpdateTimeToLive",
          "dynamodb:ListTagsOfResource",
          "dynamodb:TagResource",
          "dynamodb:UntagResource",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:DescribeContinuousBackups",
          "dynamodb:UpdateContinuousBackups"
        ]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/*-range-pulumi-locks"
      },
      {
        Sid    = "RDS"
        Effect = "Allow"
        Action = [
          "rds:CreateDBInstance",
          "rds:DeleteDBInstance",
          "rds:DescribeDBInstances",
          "rds:ModifyDBInstance",
          "rds:RebootDBInstance",
          "rds:StartDBInstance",
          "rds:StopDBInstance",
          "rds:CreateDBSubnetGroup",
          "rds:DeleteDBSubnetGroup",
          "rds:DescribeDBSubnetGroups",
          "rds:ModifyDBSubnetGroup",
          "rds:CreateDBParameterGroup",
          "rds:DeleteDBParameterGroup",
          "rds:DescribeDBParameterGroups",
          "rds:ModifyDBParameterGroup",
          "rds:DescribeDBParameters",
          "rds:AddTagsToResource",
          "rds:RemoveTagsFromResource",
          "rds:ListTagsForResource",
          "rds:DescribeDBEngineVersions",
          "rds:DescribeOrderableDBInstanceOptions"
        ]
        Resource = "*"
      },
      {
        Sid    = "ElastiCache"
        Effect = "Allow"
        Action = [
          "elasticache:CreateCacheCluster",
          "elasticache:DeleteCacheCluster",
          "elasticache:DescribeCacheClusters",
          "elasticache:ModifyCacheCluster",
          "elasticache:CreateCacheSubnetGroup",
          "elasticache:DeleteCacheSubnetGroup",
          "elasticache:DescribeCacheSubnetGroups",
          "elasticache:ModifyCacheSubnetGroup",
          "elasticache:DescribeCacheParameterGroups",
          "elasticache:DescribeCacheParameters",
          "elasticache:DescribeEngineDefaultParameters",
          "elasticache:AddTagsToResource",
          "elasticache:RemoveTagsFromResource",
          "elasticache:ListTagsForResource"
        ]
        Resource = "*"
      }
    ]
  })
}

# Security: IAM (scoped), Secrets Manager, KMS
# checkov:skip=CKV_AWS_355:CI/CD requires broad Secrets/KMS permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad Secrets/KMS permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad Secrets/KMS permissions. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad Secrets/KMS permissions. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
# IAM statements stay restricted to shifter-* naming after #253 role standardization.
resource "aws_iam_policy" "security" {
  name = "shifter-${var.environment}-security"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "IAMCreateRoleWithBoundary"
        Effect   = "Allow"
        Action   = ["iam:CreateRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*"
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = aws_iam_policy.ci_role_permissions_boundary.arn
          }
        }
      },
      {
        Sid    = "IAMRoles"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:UpdateRole",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:PutRolePolicy",
          "iam:GetRolePolicy",
          "iam:DeleteRolePolicy"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*"
      },
      {
        Sid    = "IAMAttachManagedPolicy"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*"
        Condition = {
          ArnEquals = {
            "iam:PolicyArn" = [
              "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
              "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
              "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
              "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
            ]
          }
        }
      },
      {
        Sid    = "IAMInstanceProfiles"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile",
          "iam:DeleteInstanceProfile",
          "iam:GetInstanceProfile",
          "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile",
          "iam:TagInstanceProfile",
          "iam:UntagInstanceProfile"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/shifter-*"
      },
      {
        Sid      = "IAMPassRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = [
              "ec2.amazonaws.com",
              "ecs-tasks.amazonaws.com",
              "lambda.amazonaws.com",
              "monitoring.rds.amazonaws.com",
              "vpc-flow-logs.amazonaws.com",
              "firehose.amazonaws.com",
              "logs.amazonaws.com",
              "bedrock.amazonaws.com"
            ]
          }
        }
      },
      {
        Sid      = "IAMServiceLinkedRoles"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "arn:aws:iam::*:role/aws-service-role/*"
      },
      {
        Sid    = "SecretsManager"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:PutResourcePolicy",
          "secretsmanager:DeleteResourcePolicy"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:shifter-*"
      },
      {
        Sid      = "SecretsManagerRandom"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetRandomPassword"]
        Resource = "*"
      },
      {
        Sid    = "KMS"
        Effect = "Allow"
        Action = [
          "kms:CreateKey",
          "kms:DescribeKey",
          "kms:CreateAlias",
          "kms:DeleteAlias",
          "kms:ListAliases",
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:TagResource",
          "kms:UntagResource",
          "kms:ScheduleKeyDeletion",
          "kms:GetKeyPolicy",
          "kms:PutKeyPolicy",
          "kms:EnableKeyRotation",
          "kms:GetKeyRotationStatus"
        ]
        Resource = "*"
      }
    ]
  })
}

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
          "ssm:DescribeInstanceInformation"
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
          # Range parameters for DC config
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/shifter/*/range/*",
          # AMI IDs for Kali, victim, windows, dc
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/shifter/ami/*"
        ]
      },
      {
        Sid      = "Cognito"
        Effect   = "Allow"
        Action   = ["cognito-idp:*"]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy",
          "logs:TagLogGroup",
          "logs:UntagLogGroup",
          "logs:ListTagsLogGroup",
          "logs:ListTagsForResource",
          "logs:TagResource",
          "logs:UntagResource",
          "logs:CreateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:GetLogDelivery",
          "logs:ListLogDeliveries",
          "logs:UpdateLogDelivery",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"
      },
      {
        Sid    = "CloudWatchLogsGlobal"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:GetLogDelivery",
          "logs:ListLogDeliveries",
          "logs:UpdateLogDelivery",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchAlarms"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:ListTagsForResource",
          "cloudwatch:TagResource",
          "cloudwatch:UntagResource"
        ]
        Resource = "arn:aws:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:*"
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
          "sns:GetSubscriptionAttributes"
        ]
        Resource = [
          "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-portal-*",
          "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*-range-*"
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
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# Policy Attachments
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy_attachment" "compute" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.compute.arn
}

resource "aws_iam_role_policy_attachment" "networking" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.networking.arn
}

resource "aws_iam_role_policy_attachment" "data" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.data.arn
}

resource "aws_iam_role_policy_attachment" "security" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.security.arn
}

resource "aws_iam_role_policy_attachment" "management" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.management.arn
}

# ------------------------------------------------------------------------------
# Migration: safe detach-before-attach rollout for the #254 consolidation
#
# The role already holds AWS's hard maximum of 10 managed-policy attachments.
# Going to 5 attachments cannot be done by introducing 5 brand-new attachment
# resources while the 10 old ones are orphaned: Terraform does not guarantee it
# destroys orphaned attachments before creating new ones, so the role would
# momentarily exceed 10 attachments mid-apply and AWS would reject it with
# LimitExceededException.
#
# These `moved` blocks repoint five existing attachment addresses onto the five
# consolidated policies instead. Because each address already exists in state,
# Terraform treats the policy_arn change as an in-place REPLACEMENT (policy_arn
# is ForceNew), which under the default lifecycle is destroy-before-create:
# the old policy is detached, then the new one attached, on the same address -
# the count never rises. The remaining five old attachment resources
# (core_infrastructure, elb_acm, lambda_ops, secrets_kms, network_firewall) are
# absent from the config and are destroyed (detached), taking the role from 10
# down to 5. The role therefore stays at or below 10 attachments at every point
# of the apply, with no net-new attachment addresses created.
#
# On a fresh environment with no prior state these blocks are no-ops and the
# five attachments are created normally (nothing to exceed). The blocks are
# one-time migration aids; they may be removed once every environment's global
# IAM state has been applied.
# ------------------------------------------------------------------------------

moved {
  from = aws_iam_role_policy_attachment.ec2_instances
  to   = aws_iam_role_policy_attachment.compute
}

moved {
  from = aws_iam_role_policy_attachment.vpc_networking
  to   = aws_iam_role_policy_attachment.networking
}

moved {
  from = aws_iam_role_policy_attachment.rds
  to   = aws_iam_role_policy_attachment.data
}

moved {
  from = aws_iam_role_policy_attachment.iam_scoped
  to   = aws_iam_role_policy_attachment.security
}

moved {
  from = aws_iam_role_policy_attachment.ssm_cognito
  to   = aws_iam_role_policy_attachment.management
}

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "github_actions_role_arn" {
  description = "ARN of the IAM role for GitHub Actions (add to GitHub secrets as AWS_ROLE_ARN)"
  value       = aws_iam_role.github_actions.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider"
  value       = aws_iam_openid_connect_provider.github.arn
}
