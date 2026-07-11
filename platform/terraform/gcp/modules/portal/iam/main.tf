locals {
  workload_service_accounts = toset([
    "portal",
    "workers",
    "ctf-scheduler",
    "provisioner",
  ])

  workload_identity_members = {
    portal        = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/portal]"
    workers       = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/workers]"
    ctf-scheduler = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/ctf-scheduler]"
    provisioner   = "serviceAccount:${var.project_id}.svc.id.goog[shifter-jobs/provisioner]"
  }

  node_roles = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/stackdriver.resourceMetadata.writer",
  ])

  workload_roles = {
    portal = toset([
      "roles/firebaseauth.viewer",
      "roles/pubsub.publisher",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectAdmin",
    ])
    workers = toset([
      "roles/pubsub.publisher",
      "roles/pubsub.subscriber",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectViewer",
    ])
    # The CTF scheduler polls Postgres for due tasks and triggers range
    # provisioning via cms.services.create_range, which publishes a request to
    # Pub/Sub for the provisioner to consume. It reads platform secrets at
    # startup but never subscribes, manages secrets, or touches storage, so its
    # identity is bounded to publish + secret read.
    "ctf-scheduler" = toset([
      "roles/pubsub.publisher",
      "roles/secretmanager.secretAccessor",
    ])
    provisioner = toset([
      # The provision Job runs under this identity and mints a short-lived
      # Artifact Registry access token, planting it as an imagePullSecret so the
      # isolated GDC range cluster can pull the version-matched setup-runner
      # image (RangePodSSHExecutor). AR read is the minimal grant that makes that
      # token authorize; the range cluster has no native AR pull identity.
      "roles/artifactregistry.reader",
      # The GCE range-cell backend (default per #1387) has the provisioner
      # create the range VPC, subnets, firewall rules, Cloud NAT, and guest
      # instances directly, so it needs full Compute admin on the range-cell
      # project. For the default same-project range cell this is the platform
      # project; a cross-project range cell (GCP_RANGE_CELL_PROJECT_ID) must
      # grant the equivalent role in that project (follow-up, see #1509).
      "roles/compute.admin",
      "roles/pubsub.publisher",
      # Per-instance RDP password secrets (#762) require the provisioner
      # to create, write versions to, and delete per-range secrets at
      # provisioning / teardown time. The full secret lifecycle role
      # is bounded to the provisioner workload identity and is never
      # granted to range guests, so this stays within the trust
      # boundary the SSH-key precedent established for per-range
      # secret management.
      "roles/secretmanager.admin",
      "roles/storage.objectAdmin",
    ])
  }
}

resource "google_service_account" "gke_nodes" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}nodes"
  display_name = "Shifter ${var.environment} GKE nodes"
}

resource "google_service_account" "workload" {
  for_each = local.workload_service_accounts

  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-${each.key}"
  display_name = "Shifter ${var.environment} ${each.key}"
}

resource "google_project_iam_member" "node_roles" {
  for_each = local.node_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_project_iam_member" "workload_roles" {
  for_each = merge([
    for account_name, roles in local.workload_roles : {
      for role in roles : "${account_name}:${role}" => {
        account_name = account_name
        role         = role
      }
    }
  ]...)

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.workload[each.value.account_name].email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workload_identity_members

  service_account_id = google_service_account.workload[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}

# The portal signs V4 GCS upload/download URLs (agent uploads, experiment
# artifact downloads) via the IAM credentials signBlob API: Workload Identity
# credentials carry only an access token and have no private key to sign URLs
# locally. signBlob requires the service account to be able to mint signing
# tokens for itself, so it holds serviceAccountTokenCreator scoped to its own
# identity (not a project-wide grant).
resource "google_service_account_iam_member" "portal_sign_blob" {
  service_account_id = google_service_account.workload["portal"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.workload["portal"].email}"
}

# The provisioner signs a V4 GCS download URL for each range instance's XDR
# agent object so the range VM can fetch it during provisioning. Like the
# portal, it runs under Workload Identity (token-only, no private key) and
# signs via the IAM signBlob API, which requires serviceAccountTokenCreator
# scoped to its own identity.
resource "google_service_account_iam_member" "provisioner_sign_blob" {
  service_account_id = google_service_account.workload["provisioner"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.workload["provisioner"].email}"
}

# GCE range-cell service accounts (#1509). Distinct from the workload SAs: these
# are NOT Workload-Identity-bound to a KSA. The host SA is attached to every
# range guest VM; the vertex SA backs the short-lived per-range key the a14-kali
# agent uses for Vertex AI. Created in the platform project for the default
# same-project range cell; a cross-project range cell overrides the emails and
# provisions the SAs in that project.
resource "google_service_account" "range_host" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-range-host"
  display_name = "Shifter ${var.environment} range host"
}

resource "google_service_account" "range_vertex" {
  project      = var.project_id
  account_id   = "${replace(var.name_prefix, "-", "")}-range-vertex"
  display_name = "Shifter ${var.environment} range Vertex"
}

resource "google_project_iam_member" "range_host_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/storage.objectViewer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.range_host.email}"
}

resource "google_project_iam_member" "range_vertex_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.range_vertex.email}"
}

# The provisioner attaches the host SA to range guests (actAs -> serviceAccountUser)
# and mints per-range Vertex keys on the vertex SA (serviceAccountKeyAdmin).
resource "google_service_account_iam_member" "provisioner_range_host_user" {
  service_account_id = google_service_account.range_host.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.workload["provisioner"].email}"
}

resource "google_service_account_iam_member" "provisioner_range_vertex_user" {
  service_account_id = google_service_account.range_vertex.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.workload["provisioner"].email}"
}

resource "google_service_account_iam_member" "provisioner_range_vertex_key_admin" {
  service_account_id = google_service_account.range_vertex.name
  role               = "roles/iam.serviceAccountKeyAdmin"
  member             = "serviceAccount:${google_service_account.workload["provisioner"].email}"
}
