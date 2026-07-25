# GitHub OIDC - Security Category Policy (#254)

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
        Sid    = "IAMCreateRoleWithBoundary"
        Effect = "Allow"
        Action = ["iam:CreateRole"]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
        ]
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
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
        ]
      },
      {
        Sid    = "IAMAttachManagedPolicy"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy"
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
        ]
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
        # Attach/detach account-owned customer-managed policies (e.g. the
        # ${env}-portal-pulumi-* managed policies the provisioner roles use).
        # Scoped to roles and policies under the project/env name prefixes so
        # this cannot attach arbitrary AWS-managed policies (that path stays
        # gated by IAMAttachManagedPolicy's allow-list above).
        Sid    = "IAMAttachCustomerManagedPolicy"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy"
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
        ]
        Condition = {
          ArnLike = {
            "iam:PolicyArn" = [
              "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/shifter-*",
              "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.environment}-*"
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
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/${var.environment}-*"
        ]
      },
      {
        Sid    = "IAMPassRole"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
        ]
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
              "bedrock.amazonaws.com",
              "scheduler.amazonaws.com"
            ]
          }
        }
      },
      {
        # RDS enhanced-monitoring role pass. RDS ModifyDBInstance does NOT populate the
        # iam:PassedToService context key, so the conditional IAMPassRole statement above
        # never matches and enabling enhanced monitoring fails with PassRole AccessDenied.
        # Allow passing the enhanced-monitoring roles without that condition, but scope the
        # resource tightly to *-rds-enhanced-monitoring roles. Their trust policy only permits
        # monitoring.rds.amazonaws.com, so an unconditional pass of just these roles is safe.
        Sid    = "IAMPassRoleRdsMonitoring"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-*-rds-enhanced-monitoring",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*-rds-enhanced-monitoring"
        ]
      },
      {
        Sid      = "IAMServiceLinkedRoles"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "arn:aws:iam::*:role/aws-service-role/*"
      },
      {
        Sid    = "IAMManagedPolicies"
        Effect = "Allow"
        Action = [
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:ListEntitiesForPolicy",
          "iam:TagPolicy",
          "iam:UntagPolicy"
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/shifter-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.environment}-*"
        ]
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
          "secretsmanager:DeleteResourcePolicy",
          # Enable/trigger managed rotation for the Redis AUTH secret
          # (modules/portal/redis aws_secretsmanager_secret_rotation, #159).
          "secretsmanager:RotateSecret",
          "secretsmanager:CancelRotateSecret"
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
          "kms:GetKeyRotationStatus",
          "kms:ListResourceTags",
          # Grant management: ElastiCache (and other AWS services) create a
          # grant on the customer CMK when a resource with at-rest encryption
          # is created (e.g. the portal Redis replication group). CreateGrant
          # is also needed by the apply that provisions those resources.
          "kms:CreateGrant",
          "kms:ListGrants",
          "kms:RevokeGrant",
          "kms:ReEncrypt*",
          "kms:GenerateDataKeyWithoutPlaintext"
        ]
        Resource = "*"
      }
    ]
  })
}
