# Portal Composition - kms
#
# Customer-managed keys for Secrets Manager, the portal S3 bucket, and Redis at-rest encryption.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).

# Portal Composition Module (AWS)
#
# The repeated dev/proof/prod portal resource graph, extracted so a new AWS
# environment is a thin root selecting backend + tfvars rather than another
# copied main.tf (#688). Environment roots keep provider/backend/lockfile
# ownership, terraform_remote_state reads, and the public variable and output
# contracts; this module owns the resource graph and its internal wiring.
#
# Environment variation travels on explicit typed inputs (enable_ctfd,
# deletion protection, warm pool, alarms), never on the environment name.

# ------------------------------------------------------------------------------
# KMS CMKs — Secrets Manager and Portal S3 bucket
# ------------------------------------------------------------------------------
# Closes Checkov CKV_AWS_149 (Secrets Manager CMK) and CKV_AWS_145 (S3 SSE-KMS)
# for #213 / #218. The `kms:ViaService` + `kms:CallerAccount` condition is the
# AWS-recommended pattern for service-scoped CMKs: anyone in this account who
# already holds `secretsmanager:GetSecretValue` (or `s3:GetObject`) on the
# specific resource can transparently decrypt through the service; principals
# from other accounts cannot. Annual key rotation is enabled automatically
# (`enable_key_rotation = true`).
#
# These keys are intentionally separate from `engine-state` (Pulumi state) and
# from each other, so a future revoke/rotate of one boundary does not collapse
# the others. See docs/architecture/secrets-manager-cmk-preflight.md and
# docs/architecture/s3-bucket-hardening-preflight.md.

resource "aws_kms_key" "secrets_manager" {
  description             = "CMK for portal Secrets Manager secrets (CKV_AWS_149) — see #213"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # Account-scoped use via Secrets Manager only, AND bound by encryption
        # context to portal-owned secret ARNs (`shifter-<env>-*` for platform
        # secrets and `shifter/<env>/*` for engine-provisioner runtime secrets).
        # Secrets Manager always passes `SecretARN` as encryption context, so
        # `kms:EncryptionContext:SecretARN` constrains use of this key to the
        # specific secret namespace this CMK is intended to protect — a
        # principal with `kms:Decrypt` could not use this key to decrypt some
        # other Secrets Manager secret in the account.
        Sid       = "AllowPortalSecretsManagerCallers"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:CreateGrant",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
            "kms:ViaService"    = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
          "ForAnyValue:StringLike" = {
            "kms:EncryptionContext:SecretARN" = [
              "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:shifter-${var.environment}-*",
              "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:shifter/${var.environment}/*",
            ]
          }
        }
      },
    ]
  })

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-secrets-manager"
  })
}

resource "aws_kms_alias" "secrets_manager" {
  name          = "alias/shifter-${var.environment}-secrets-manager"
  target_key_id = aws_kms_key.secrets_manager.key_id
}

resource "aws_kms_key" "portal_s3" {
  description             = "CMK for the portal user-uploads S3 bucket (CKV_AWS_145) — see #218"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # Account-scoped use via S3 only, AND bound by encryption context to
        # objects under the portal user-uploads bucket. S3 always passes
        # `aws:s3:arn = arn:aws:s3:::<bucket>/<key>` as encryption context for
        # SSE-KMS, so this condition constrains use of this key to objects in
        # the configured bucket — a principal with `kms:Decrypt` could not use
        # this key to decrypt some other S3 object in the account.
        Sid       = "AllowPortalUserUploadsBucket"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
            "kms:ViaService"    = "s3.${var.aws_region}.amazonaws.com"
          }
          "ForAnyValue:StringLike" = {
            # With S3 Bucket Keys enabled (set in `modules/portal/s3`), S3
            # passes the BUCKET ARN as KMS encryption context for the per-bucket
            # data key. For object-level operations without Bucket Keys S3
            # passes the OBJECT ARN. Allow both patterns so the policy doesn't
            # deny the first SSE-KMS operation.
            "kms:EncryptionContext:aws:s3:arn" = [
              "arn:aws:s3:::${var.user_storage_bucket}",
              "arn:aws:s3:::${var.user_storage_bucket}/*",
            ]
          }
        }
      },
    ]
  })

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-s3"
  })
}

resource "aws_kms_alias" "portal_s3" {
  name          = "alias/shifter-${var.environment}-portal-s3"
  target_key_id = aws_kms_key.portal_s3.key_id
}

resource "aws_kms_key" "redis_at_rest" {
  description             = "CMK for portal Redis (ElastiCache) data-at-rest encryption (CKV_AWS_191) — see #1059"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # Account-scoped use via ElastiCache only. ElastiCache uses this CMK on
        # the account's behalf — creating grants for the replication group — to
        # encrypt cache storage and the group's automated snapshots. kms:ViaService
        # constrains every use/grant of this key to the ElastiCache service in
        # this region, and kms:CallerAccount pins it to this account. No runtime
        # EC2/ECS role needs a direct decrypt grant: at-rest encryption is
        # provider-owned storage encryption, distinct from the Secrets Manager
        # CMK that protects the Redis AUTH token.
        Sid       = "AllowPortalElastiCacheUse"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:CreateGrant",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
            "kms:ViaService"    = "elasticache.${var.aws_region}.amazonaws.com"
          }
        }
      },
    ]
  })

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-redis-at-rest"
  })
}

resource "aws_kms_alias" "redis_at_rest" {
  name          = "alias/shifter-${var.environment}-redis-at-rest"
  target_key_id = aws_kms_key.redis_at_rest.key_id
}
