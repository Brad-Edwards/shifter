# Portal Composition - storage
#
# Portal S3 bucket and the range-instance read grant over it.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# S3 User Storage
# ------------------------------------------------------------------------------

module "s3" {
  source = "../s3"

  bucket_name          = var.user_storage_bucket
  cors_allowed_origins = ["https://${var.domain_name}"]
  kms_key_arn          = aws_kms_key.portal_s3.arn
  tags                 = var.tags
}

resource "aws_iam_role_policy" "range_instance_portal_s3_kms_read" {
  name = "portal-s3-kms-read"
  role = replace(var.range_range_instance_role_arn, "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/", "")

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = aws_kms_key.portal_s3.arn
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
