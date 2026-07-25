# Engine Provisioner - ECS Task Role and Data Grants
#
# The provisioner container's own identity, plus engine state, Secrets Manager,
# RDS IAM auth, the agent bucket, and the KMS grant backing secret access.
# Secretsmanager and kms:Decrypt grants are colocated with the role on purpose
# (see iam_execution.tf header, #688). Compute, network, and SSM privileges
# live in iam_*.tf siblings.

# ------------------------------------------------------------------------------
# ECS Task Role
# ------------------------------------------------------------------------------
# Used by the engine provisioner container for AWS operations

resource "aws_iam_role" "ecs_task" {
  name                 = "${local.iam_name_prefix}-pulumi-ecs-task"
  permissions_boundary = var.permissions_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = local.common_tags
}

# ------------------------------------------------------------------------------
# Task Role Policy - Engine State
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy" "engine_state" {
  name = "pulumi-state"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          var.engine_state_bucket_arn,
          "${var.engine_state_bucket_arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem"
        ]
        Resource = var.engine_locks_table_arn
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - Secrets Manager
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy" "secrets_manager" {
  name = "secrets-manager"
  role = aws_iam_role.ecs_task.id

  # Permissions based on Terraform AWS provider source code analysis:
  # - secret.go: CreateSecret, DescribeSecret, GetResourcePolicy, DeleteSecret
  # - secret_version.go: PutSecretValue, GetSecretValue, ListSecretVersionIds, UpdateSecretVersionStage
  # Ref: github.com/hashicorp/terraform-provider-aws/internal/service/secretsmanager/
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        # secret.go - resourceSecretCreate, resourceSecretRead, resourceSecretDelete
        "secretsmanager:CreateSecret",
        "secretsmanager:TagResource",
        "secretsmanager:DescribeSecret",
        "secretsmanager:GetResourcePolicy",
        "secretsmanager:DeleteSecret",
        # secret_version.go - resourceSecretVersionCreate, resourceSecretVersionRead, resourceSecretVersionDelete
        "secretsmanager:PutSecretValue",
        "secretsmanager:GetSecretValue",
        "secretsmanager:ListSecretVersionIds",
        "secretsmanager:UpdateSecretVersionStage"
      ]
      Resource = [
        "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:shifter/${var.environment}/range/*",
        "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:shifter/${var.environment}/vpn-issuer/*",
        "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:shifter/${var.environment}/ngfw/*"
      ]
    }]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - RDS IAM Authentication
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy" "rds_iam_auth" {
  name = "rds-iam-auth"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "rds-db:connect"
      Resource = "arn:aws:rds-db:${local.region}:${local.account_id}:dbuser:${var.db_resource_id}/provisioner_lambda"
    }]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - S3 Agent Bucket
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy" "s3_agent" {
  name = "s3-agent-read"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:ListBucket"
      ]
      Resource = [
        var.agent_s3_bucket_arn,
        "${var.agent_s3_bucket_arn}/*"
      ]
    }]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - KMS (for engine secrets encryption)
# ------------------------------------------------------------------------------
# The engine's awskms:// secrets provider calls KMS directly (not via Secrets Manager),
# so we need separate statements for each use case.

resource "aws_iam_role_policy" "kms" {
  name = "kms-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        # Dedicated CMK for engine stack secrets encryption
        Resource = var.engine_secrets_kms_key_arn
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${local.region}.amazonaws.com"
          }
        }
      }
    ]
  })
}
