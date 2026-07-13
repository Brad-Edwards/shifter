locals {
  workload_service_accounts = toset([
    "portal",
    "workers",
    "ctf-scheduler",
    "provisioner-launcher",
    "provisioner",
  ])

  workload_identity_members = {
    portal               = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/portal]"
    workers              = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/workers]"
    ctf-scheduler        = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/ctf-scheduler]"
    provisioner-launcher = "serviceAccount:${var.project_id}.svc.id.goog[shifter-platform/provisioner-launcher]"
    provisioner          = "serviceAccount:${var.project_id}.svc.id.goog[shifter-jobs/provisioner]"
  }

  node_roles = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/stackdriver.resourceMetadata.writer",
  ])

  # ADR-008-R7 (docs/architecture/gcp-workload-resource-iam-preflight-1517.md):
  # workload identities receive project-level IAM only for APIs whose Google
  # roles are inherently project scoped (Firebase Auth, Pub/Sub, Artifact
  # Registry, Compute). Secret Manager payload access and Cloud Storage object
  # access are bound per named resource below, never at project scope. The
  # check_tf_gcp_iam_resource_scope guard fails closed on any secret/storage
  # payload/admin role added here.
  workload_project_roles = {
    portal = toset([
      "roles/firebaseauth.viewer",
      "roles/pubsub.publisher",
    ])
    workers = toset([
      "roles/pubsub.publisher",
      "roles/pubsub.subscriber",
    ])
    # The CTF scheduler polls Postgres for due tasks and triggers range
    # provisioning via cms.services.create_range, which publishes a request to
    # Pub/Sub for the provisioner to consume. It reads platform secrets at
    # startup (bound per named secret below) but never subscribes or touches
    # storage, so its project identity is bounded to publish.
    "ctf-scheduler" = toset([
      "roles/pubsub.publisher",
    ])
    # The launch worker reaches Postgres and Kubernetes only. It receives no
    # project-scoped cloud role and is deliberately distinct from provisioner.
    provisioner-launcher = toset([])
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
    ])
  }

  # Named runtime secrets the shared portal image hydrates at container startup
  # via entrypoint.sh (app, db, guacamole-json-auth, dc-domain-password, redis,
  # and email when configured). guacamole-db is excluded: it is delivered to the
  # separate guacamole-client pod as a native Kubernetes Secret, never fetched
  # from Secret Manager by these identities. The provisioner is absent: it
  # receives DB / field-key / DC-password values through its per-Job ephemeral
  # Kubernetes Secret and does not read runtime bundles from Secret Manager.
  secret_reader_workloads    = toset(["portal", "workers", "ctf-scheduler", "provisioner-launcher"])
  runtime_secret_reader_keys = [for key in keys(var.runtime_secret_ids) : key if key != "guacamole-db"]
  workload_secret_bindings = {
    for pair in setproduct(tolist(local.secret_reader_workloads), local.runtime_secret_reader_keys) :
    "${pair[0]}:${pair[1]}" => {
      workload  = pair[0]
      secret_id = var.runtime_secret_ids[pair[1]]
    }
  }

  # Per-bucket object access. Assets bucket: portal read/write (uploads,
  # finalize, delete, signed URLs), workers read-only, provisioner read-only
  # (agent-installer signed URL). Provisioner also owns per-range Terraform /
  # Pulumi state (read/write) and, when configured, the VM-Series bootstrap
  # bucket (read/write).
  workload_bucket_bindings = merge(
    {
      "portal:assets"      = { workload = "portal", bucket = var.assets_bucket_name, role = "roles/storage.objectAdmin" }
      "workers:assets"     = { workload = "workers", bucket = var.assets_bucket_name, role = "roles/storage.objectViewer" }
      "provisioner:assets" = { workload = "provisioner", bucket = var.assets_bucket_name, role = "roles/storage.objectViewer" }
      "provisioner:state"  = { workload = "provisioner", bucket = var.terraform_state_bucket_name, role = "roles/storage.objectAdmin" }
    },
    var.vmseries_bootstrap_bucket_name == "" ? {} : {
      "provisioner:vmseries" = { workload = "provisioner", bucket = var.vmseries_bootstrap_bucket_name, role = "roles/storage.objectAdmin" }
    },
  )
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
    for account_name, roles in local.workload_project_roles : {
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

# Static runtime-secret reads, bound per named secret (ADR-008-R7). Replaces the
# former project-level roles/secretmanager.secretAccessor grant on portal,
# workers, and ctf-scheduler.
resource "google_secret_manager_secret_iam_member" "workload_secret_readers" {
  for_each = local.workload_secret_bindings

  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workload[each.value.workload].email}"
}

# Cloud Storage object access, bound per named bucket (ADR-008-R7). Replaces the
# former project-level roles/storage.objectAdmin / objectViewer grants on
# portal, workers, and provisioner.
resource "google_storage_bucket_iam_member" "workload_buckets" {
  for_each = local.workload_bucket_bindings

  bucket = each.value.bucket
  role   = each.value.role
  member = "serviceAccount:${google_service_account.workload[each.value.workload].email}"
}

# Residual project-level dynamic-secret grants (tracked by #1586).
#
# These two grants stay project scoped because the provisioner-created per-range
# guest-credential secrets cannot be resource-name scoped yet: `secrets.create`
# is authorized on the parent project before the secret exists, and the four
# naming helpers (gcp_guest_secrets, _gdc_vm_naming, gcp_range_vertex_creds,
# gdc_vmseries_common) use divergent prefixes, so a single resource-name IAM
# condition is unsafe. The dedicated range-secret project / broker boundary that
# removes both grants is designed in #1586. check_tf_gcp_iam_resource_scope
# allowlists exactly these two (workload, role) pairs with an expiry; every other
# project-level secret/storage grant on a workload identity fails the guard.

# Portal reads per-range *-ssh / *-rdp-password guest credentials at runtime
# (shifter_platform engine/secrets.py get_ssh_key / get_rdp_password) for the
# SSH terminal and Guacamole RDP connection flows.
resource "google_project_iam_member" "portal_dynamic_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.workload["portal"].email}"
}

# Provisioner creates, versions, reads, deletes, and (for per-range Vertex keys)
# sets IAM policy on the dynamic guest-credential secrets, and reads
# operator-provided GDC secret references.
resource "google_project_iam_member" "provisioner_dynamic_secret_admin" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.workload["provisioner"].email}"
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
