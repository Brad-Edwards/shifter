# Portal EC2 - Data Access Privileges
#
# RDS IAM authentication, S3 object access, the ACES package bucket, and the
# KMS grant covering S3 object encryption.

# IAM policy for RDS IAM database authentication (#159).
# The long-running portal (web + workers) connects to the database as the
# dedicated rds_iam runtime user with a short-lived token instead of a stored
# password (config.db_backends.rds_iam; mission_control migration 0041 creates
# the user). Scoped to that single DB user on this RDS instance's resource id,
# mirroring modules/engine-provisioner/iam.tf's rds_iam_auth grant for the
# provisioner Lambda user.
resource "aws_iam_role_policy" "rds_iam_auth" {
  name = "rds-iam-auth"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "rds-db:connect"
        Resource = "arn:aws:rds-db:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:dbuser:${var.db_resource_id}/${var.db_iam_runtime_user}"
      }
    ]
  })
}

resource "aws_iam_role_policy" "s3_access" {
  name = "s3-access"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:PutObjectTagging",
          "s3:GetObjectTagging"
        ]
        Resource = "${var.s3_bucket_arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = var.s3_bucket_arn
      }
    ]
  })
}

# Least-privilege, read-only access to the object-backed ACES package bucket
# (#1567, ADR-034-R5). The portal pulls the single immutable pack archive at
# launch and nothing else: GetObject is scoped to the optional key prefix, and
# ListBucket is constrained by an s3:prefix condition. Created only when a
# package bucket is configured, so deployments not using object-backed packs get
# no additional grant.
resource "aws_iam_role_policy" "aces_package_read" {
  count = var.aces_package_bucket_arn != "" ? 1 : 0
  name  = "aces-package-read"
  role  = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.aces_package_bucket_arn}/${var.aces_package_prefix}*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.aces_package_bucket_arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.aces_package_prefix}*"]
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "s3_kms" {
  name = "s3-kms-access"
  role = aws_iam_role.this.id

  # The portal user-storage bucket is SSE-KMS encrypted with a CMK and its
  # bucket policy *enforces* that CMK (modules/portal/s3). The CMK key policy
  # grants the account root for the s3 ViaService path, which delegates the
  # decision to IAM — so the instance role must hold kms:GenerateDataKey
  # (uploads) and kms:Decrypt (downloads) on the key or every challenge-file
  # PutObject/GetObject fails with AccessDenied / SNS-style KMS errors.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = var.s3_kms_key_arn
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
            "kms:ViaService"    = "s3.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}
