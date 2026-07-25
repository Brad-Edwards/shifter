# GitHub OIDC - Data Category Policy (#254)

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
          # Prod state bucket (shifter-infra-*) and per-environment state
          # buckets (shifter-dev-infra-*, shifter-proof-infra-*, ...).
          "arn:aws:s3:::shifter-infra-*",
          "arn:aws:s3:::shifter-infra-*/*",
          "arn:aws:s3:::shifter-*-infra-*",
          "arn:aws:s3:::shifter-*-infra-*/*"
        ]
      },
      {
        Sid    = "S3BakeBucketsRead"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetObject"
        ]
        # Scenario bake buckets (e.g. shifter-polaris-bake-<account>). The
        # polaris bake verifies the operator-uploaded build tarball exists
        # before standing up a golden range. Read-only: the operator uploads
        # the tarball out of band and the range instance role (granted in
        # scripts/polaris-aws-range) does the actual download.
        Resource = [
          "arn:aws:s3:::shifter-*-bake-*",
          "arn:aws:s3:::shifter-*-bake-*/*"
        ]
      },
      {
        Sid    = "S3UserStorage"
        Effect = "Allow"
        Action = ["s3:*"]
        Resource = [
          "arn:aws:s3:::shifter-user-storage-*",
          "arn:aws:s3:::shifter-*-user-storage-*"
        ]
      },
      {
        Sid    = "S3PortalBuckets"
        Effect = "Allow"
        Action = ["s3:*"]
        Resource = [
          # Portal-owned buckets (logs, ALB access logs, etc.) named {env}-portal-*.
          "arn:aws:s3:::*-portal-*",
          "arn:aws:s3:::*-portal-*/*"
        ]
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
          # Bucket names carry an account-id suffix (e.g. proof-range-pulumi-state-<acct>),
          # so match the prefix with a trailing wildcard.
          "arn:aws:s3:::*-range-pulumi-state*",
          "arn:aws:s3:::*-range-pulumi-state*/*"
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
          "rds:DescribeOrderableDBInstanceOptions",
          "rds:CreateEventSubscription",
          "rds:DeleteEventSubscription",
          "rds:ModifyEventSubscription",
          "rds:DescribeEventSubscriptions",
          "rds:AddSourceIdentifierToSubscription",
          "rds:RemoveSourceIdentifierFromSubscription"
        ]
        Resource = "*"
      },
      {
        # ElastiCache (Redis) replication groups, clusters, and subnet groups for
        # the portal. Describe/tag actions are not ARN-addressable, so the
        # statement scopes by action and keeps Resource "*".
        Sid    = "ElastiCache"
        Effect = "Allow"
        Action = [
          "elasticache:DescribeCacheClusters",
          "elasticache:DescribeReplicationGroups",
          "elasticache:DescribeCacheSubnetGroups",
          "elasticache:CreateCacheCluster",
          "elasticache:DeleteCacheCluster",
          "elasticache:ModifyCacheCluster",
          "elasticache:CreateReplicationGroup",
          "elasticache:DeleteReplicationGroup",
          "elasticache:ModifyReplicationGroup",
          "elasticache:CreateCacheSubnetGroup",
          "elasticache:DeleteCacheSubnetGroup",
          "elasticache:ModifyCacheSubnetGroup",
          "elasticache:ListTagsForResource",
          "elasticache:AddTagsToResource",
          "elasticache:RemoveTagsFromResource"
        ]
        Resource = "*"
      }
    ]
  })
}
