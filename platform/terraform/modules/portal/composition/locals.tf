# Naming and derived values shared across the portal composition.

locals {
  name_prefix                      = "${var.environment}-portal"
  iam_name_prefix                  = "shifter-${var.environment}-portal"
  ci_role_permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/shifter-${var.environment}-ci-role-boundary"
  alb_access_logs_bucket_name      = "${local.name_prefix}-alb-logs-${var.environment}-${data.aws_caller_identity.current.account_id}"
  # Add padding to field_encryption_key (b64_url doesn't include padding, but Fernet requires it)
  field_encryption_key_padded = "${random_id.field_encryption_key.b64_url}="
}
