resource "random_password" "db_password" {
  length  = 32
  special = true

  # Rotation 1 repairs tenants whose write-only Cloud SQL user password
  # drifted from the Terraform-managed Secret Manager payload. Increment this
  # value for an intentional future rotation so the SQL user and secret
  # version are updated together.
  keepers = {
    rotation = 1
  }
}

resource "random_password" "guacamole_db_password" {
  length  = 32
  special = true
}

resource "google_sql_database_instance" "platform" {
  name                = "${var.name_prefix}-pg"
  project             = var.project_id
  region              = var.region
  database_version    = var.cloud_sql_database_version
  deletion_protection = var.cloud_sql_deletion_protection

  settings {
    tier                        = var.cloud_sql_tier
    availability_type           = var.cloud_sql_availability_type
    disk_size                   = var.cloud_sql_disk_size_gb
    disk_type                   = "PD_SSD"
    deletion_protection_enabled = var.cloud_sql_deletion_protection

    backup_configuration {
      enabled = true
    }

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.platform_network_id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "log_connections"
      value = "on"
    }

    user_labels = var.common_labels
  }

  # Cloud SQL may grow an auto-resized disk but cannot shrink it. Reconciling
  # the configured floor after growth would otherwise plan a destructive
  # replacement of the deletion-protected production database.
  lifecycle {
    ignore_changes = [settings[0].disk_size]
  }
}

resource "google_sql_database" "platform" {
  name     = var.cloud_sql_database_name
  project  = var.project_id
  instance = google_sql_database_instance.platform.name
}

resource "google_sql_database" "guacamole" {
  name     = "guacamole"
  project  = var.project_id
  instance = google_sql_database_instance.platform.name
}

resource "google_sql_user" "platform" {
  name     = var.cloud_sql_user_name
  project  = var.project_id
  instance = google_sql_database_instance.platform.name
  password = random_password.db_password.result
}

resource "google_sql_user" "guacamole" {
  name     = "guacamole_admin"
  project  = var.project_id
  instance = google_sql_database_instance.platform.name
  password = random_password.guacamole_db_password.result
}
