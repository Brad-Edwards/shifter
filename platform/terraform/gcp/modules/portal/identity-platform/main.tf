data "archive_file" "identity_platform_before_create" {
  type        = "zip"
  source_dir  = "${path.module}/functions/identity-platform"
  output_path = "${path.root}/.terraform/${var.name_prefix}-identity-platform-before-create.zip"
}

resource "google_storage_bucket_object" "identity_platform_before_create" {
  name   = "identity-platform/identity-platform-before-create-${data.archive_file.identity_platform_before_create.output_md5}.zip"
  bucket = var.assets_bucket_name
  source = data.archive_file.identity_platform_before_create.output_path
}

resource "google_cloudfunctions_function" "identity_platform_before_create" {
  name                  = "${var.name_prefix}-identity-before-create"
  project               = var.project_id
  region                = var.region
  runtime               = "nodejs18"
  available_memory_mb   = 128
  timeout               = 10
  source_archive_bucket = var.assets_bucket_name
  source_archive_object = google_storage_bucket_object.identity_platform_before_create.name
  trigger_http          = true
  entry_point           = "beforeCreate"

  environment_variables = {
    ALLOWED_EMAIL_DOMAIN = var.identity_allowed_email_domain
    ALLOWED_EMAILS       = join(",", var.identity_allowed_emails)
  }
}

resource "google_cloudfunctions_function_iam_member" "identity_platform_before_create_invoker" {
  project        = var.project_id
  region         = var.region
  cloud_function = google_cloudfunctions_function.identity_platform_before_create.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"
}

resource "google_identity_platform_config" "platform" {
  project = var.project_id

  authorized_domains = var.identity_authorized_domains

  sign_in {
    allow_duplicate_emails = false

    anonymous {
      enabled = false
    }

    email {
      enabled           = true
      password_required = true
    }

    phone_number {
      enabled = false
    }
  }

  client {
    permissions {
      disabled_user_deletion = true
      disabled_user_signup   = false
    }
  }

  blocking_functions {
    triggers {
      event_type   = "beforeCreate"
      function_uri = google_cloudfunctions_function.identity_platform_before_create.https_trigger_url
    }
  }

  mfa {
    state = "ENABLED"

    provider_configs {
      state = "ENABLED"

      totp_provider_config {
        adjacent_intervals = 1
      }
    }
  }

  monitoring {
    request_logging {
      enabled = true
    }
  }
}
