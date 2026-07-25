# Portal Composition - secrets
#
# Generated app secrets and their Secrets Manager entries.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# App Secret (Django secret key)
# ------------------------------------------------------------------------------

resource "random_password" "django_secret_key" {
  length  = 50
  special = true
}

# Fernet encryption key for django-encrypted-model-fields (32 bytes, base64-encoded)
resource "random_id" "field_encryption_key" {
  byte_length = 32
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "shifter-${local.name_prefix}-app"
  description             = "Django application secrets"
  recovery_window_in_days = var.secret_recovery_window_in_days # NOSONAR - 0 in disposable environments avoids naming conflicts on recreate
  kms_key_id              = aws_kms_key.secrets_manager.arn

  tags = merge(var.tags, {
    Name = "shifter-${local.name_prefix}-app"
  })
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    django_secret_key    = random_password.django_secret_key.result
    field_encryption_key = local.field_encryption_key_padded
  })
}
