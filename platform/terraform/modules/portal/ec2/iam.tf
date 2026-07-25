# Portal EC2 - Instance Identity and Secret-Bearing Grants
#
# The instance role, its instance profile, and every grant that reads a
# Secrets Manager value plus the KMS grant that makes those reads work.
# These are deliberately kept in one file: check_tf_kms_secrets_grant pairs a
# role with its kms:Decrypt grant per file, so separating them would make the
# check pass without verifying anything (#688).
# Non-secret privilege groups live in iam_*.tf siblings.

# ------------------------------------------------------------------------------
# IAM Role for EC2
# ------------------------------------------------------------------------------

resource "aws_iam_role" "this" {
  name                 = "${local.iam_name_prefix}-ec2-role"
  permissions_boundary = var.permissions_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "secrets_read" {
  name = "secrets-read"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = var.secret_arns
      }
    ]
  })
}

# Allow the portal EC2 role to decrypt secrets encrypted with the portal
# Secrets Manager CMK. The portal container reads values via boto3 from inside
# the container, but the underlying Secrets Manager → KMS Decrypt call runs as
# this EC2 instance role and needs kms:Decrypt on the CMK. Without it,
# `entrypoint.sh::fetch_runtime_secret` fails the GetSecretValue call with
# `AccessDeniedException: Access to KMS is not allowed`, and the existing
# bug-fix to entrypoint.sh aborts container start (better than silently
# exporting an empty env var). Scoped to the concrete CMK ARN and pinned to
# Secrets Manager via kms:ViaService. See issue #52.
resource "aws_iam_role_policy" "kms_secrets_decrypt" {
  name = "kms-secrets-decrypt"
  role = aws_iam_role.this.id

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
          "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
        }
      }
    }]
  })
}

# IAM policy for reading range SSH keys from Secrets Manager
# SSH keys are stored at: shifter/{env}/range/{range_id}/*-ssh-key
# Required for Terminal UI feature to connect to Kali/Victim instances
resource "aws_iam_role_policy" "range_ssh_keys" {
  name = "range-ssh-keys-read"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:shifter/*/range/*"
      }
    ]
  })
}

# IAM policy for reading NGFW SSH keys from Secrets Manager
# SSH keys are stored at: shifter/{env}/ngfw/{instance_uuid}/ssh-key
# Required for NGFW CLI access feature via Guacamole SSH
resource "aws_iam_role_policy" "ngfw_ssh_keys" {
  name = "ngfw-ssh-keys-read"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:shifter/*/ngfw/*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "this" {
  name = "${local.iam_name_prefix}-ec2-profile"
  role = aws_iam_role.this.name

  tags = local.common_tags
}
