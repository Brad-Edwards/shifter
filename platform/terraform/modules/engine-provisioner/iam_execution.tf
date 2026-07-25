# Engine Provisioner - ECS Execution Role
#
# Identity ECS itself assumes to pull images and hydrate task-definition
# secrets. The secretsmanager grant and its kms:Decrypt counterpart stay in
# this file: check_tf_kms_secrets_grant pairs them per file, so splitting them
# would make the check pass without verifying anything (#688).

# ------------------------------------------------------------------------------
# ECS Execution Role
# ------------------------------------------------------------------------------
# Used by ECS to pull container images and write logs

resource "aws_iam_role" "ecs_execution" {
  name                 = "${local.iam_name_prefix}-pulumi-ecs-execution"
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

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_ecr" {
  name = "ecr-pull"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ]
      Resource = "*"
    }]
  })
}

# Allow the ECS execution role to fetch the DC domain password secret so
# ECS can hydrate the DC_DOMAIN_PASSWORD container env var via the
# `secrets = [...]` block in task_definition.tf.
resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "secrets-read"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = [
        aws_secretsmanager_secret.dc_domain_password.arn
      ]
    }]
  })
}

# Allow the ECS execution role to decrypt secrets encrypted with the portal
# Secrets Manager CMK. ECS resolves task-definition `secrets = [...]` before
# container start using the execution role, so a missing kms:Decrypt grant on
# the CMK aborts the task with `ResourceInitializationError: Access to KMS is
# not allowed` and the container never runs. Mirrors `SecretsManagerKMSAccess`
# on the task role below, but pinned to the concrete CMK ARN (preflight
# guidance: prefer the concrete CMK ARN when the role only needs the portal
# CMK). See issue #52.
resource "aws_iam_role_policy" "ecs_execution_kms" {
  name = "kms-secrets-decrypt"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "SecretsManagerKMSAccess"
      Effect = "Allow"
      Action = [
        "kms:Decrypt",
        "kms:DescribeKey"
      ]
      Resource = var.secrets_manager_kms_key_arn
      Condition = {
        StringEquals = {
          "kms:ViaService" = "secretsmanager.${local.region}.amazonaws.com"
        }
      }
    }]
  })
}
