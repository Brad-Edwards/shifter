resource "google_container_cluster" "platform" {
  name     = "${var.name_prefix}-gke"
  project  = var.project_id
  location = var.region

  network    = var.platform_network_id
  subnetwork = var.gke_subnetwork_id

  deletion_protection      = false
  remove_default_node_pool = true
  initial_node_count       = 1

  # The default node pool is created then immediately removed
  # (remove_default_node_pool). Pin it to the dedicated GKE node SA rather than
  # letting it fall back to the project default compute SA: the scoped CI deploy
  # identity only holds actAs on the node SA (modules/portal/iam
  # deploy_act_as_gke_nodes), not the default compute SA, so without this the
  # cluster create fails with "does not have access to
  # <project-number>-compute@developer.gserviceaccount.com". It also keeps the
  # transient pool off the broad default compute SA.
  node_config {
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  networking_mode = "VPC_NATIVE"

  # GKE Dataplane V2 (#1295): a Standard, VPC-native cluster does NOT enforce
  # Kubernetes NetworkPolicy unless an enforcing datapath is selected. Without
  # this, the committed default-deny NetworkPolicies (platform/k8s/gcp/base/
  # networkpolicies.yaml: default-deny-platform / default-deny-jobs) are admitted
  # by the API server but silently unenforced, so the "default-deny network
  # boundary" the control plane relies on does not exist. ADVANCED_DATAPATH is
  # the Cilium-based Dataplane V2 provider; it enforces NetworkPolicy natively
  # (and is mutually exclusive with the legacy Calico `network_policy` addon).
  # This mirrors the AWS/EKS posture, where the vpc-cni NetworkPolicy agent is
  # enabled so the same chart NetworkPolicies are enforced rather than rendered.
  # Note: datapath_provider is set at creation; adopting it on an existing
  # cluster requires a recreate (an operator step, not a code concern here).
  datapath_provider = "ADVANCED_DATAPATH"

  ip_allocation_policy {
    cluster_secondary_range_name  = var.gke_pods_secondary_range_name
    services_secondary_range_name = var.gke_services_secondary_range_name

    additional_pod_ranges_config {
      pod_range_names = [
        var.gke_provisioner_pods_secondary_range_name,
        var.gke_access_pods_secondary_range_name,
      ]
    }
  }

  # Private control plane, secure by default (#1723): no public IP endpoint on the
  # control plane (satisfies org policies such as custom.disableGkePublicControlPlane
  # and is universally the hardened posture). Operator and CI reach the control
  # plane through Connect Gateway via the fleet membership below (IAM-authenticated,
  # no public IP, no DNS endpoint, no bastion), so private-by-default does not cost
  # remote access.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
    master_ipv4_cidr_block  = var.gke_master_ipv4_cidr
  }

  # Always present (enabled): a private control plane requires
  # master_authorized_networks_config to be enabled. cidr_blocks may be empty —
  # remote access is via the IAM-authenticated DNS endpoint, not a network
  # allowlist (#1723). gcp_public_cidrs_access_enabled defaults false.
  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.gke_master_authorized_cidrs

      content {
        cidr_block   = cidr_blocks.value
        display_name = "admin-${replace(replace(cidr_blocks.value, "/", "-"), ".", "-")}"
      }
    }
  }

  release_channel {
    channel = var.gke_release_channel
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Register the cluster to the project fleet so operator/CI reach the private
  # control plane through Connect Gateway (IAM-authenticated, no public endpoint,
  # no DNS endpoint, no bastion) — the access path for a private control plane
  # when both the public endpoint and the Google DNS endpoint are disallowed
  # (#1723). Credentials: `gcloud container fleet memberships get-credentials`.
  fleet {
    project = var.project_id
  }

  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
  }

  resource_labels = var.common_labels
}

resource "google_container_node_pool" "web" {
  name       = "${var.name_prefix}-web"
  project    = var.project_id
  location   = var.region
  cluster    = google_container_cluster.platform.name
  node_count = var.web_node_count

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.web_machine_type
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = merge(var.common_labels, { role = "web" })
    tags            = ["shifter", "gke", "web"]

    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}

resource "google_container_node_pool" "workers" {
  name       = "${var.name_prefix}-workers"
  project    = var.project_id
  location   = var.region
  cluster    = google_container_cluster.platform.name
  node_count = var.worker_node_count

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.worker_machine_type
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = merge(var.common_labels, { role = "workers" })
    tags            = ["shifter", "gke", "workers"]

    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}

resource "google_container_node_pool" "provisioner" {
  name       = "${var.name_prefix}-provisioner"
  project    = var.project_id
  location   = var.region
  cluster    = google_container_cluster.platform.name
  node_count = var.provisioner_node_count

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  network_config {
    # Private nodes (no external IP): egress is via Cloud NAT, matching the web/
    # workers pools and the cluster default. Specifying network_config without
    # enable_private_nodes otherwise defaults it to false (public/external IP),
    # which is both a needless exposure and a violation of the
    # constraints/compute.vmExternalIpAccess org policy (#1723).
    enable_private_nodes = true
    create_pod_range     = false
    pod_range            = var.gke_provisioner_pods_secondary_range_name
  }

  node_config {
    machine_type    = var.provisioner_machine_type
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    # `role` is retained for observability, but the security-relevant nodeSelector
    # keys on node-restriction.kubernetes.io/shifter-pool: NodeRestriction forbids
    # a (compromised) kubelet from setting that prefix on its own Node, so a
    # rogue worker cannot self-label to attract provisioner Jobs (#1711 codex).
    labels = merge(var.common_labels, {
      role                                          = "provisioner"
      "node-restriction.kubernetes.io/shifter-pool" = "provisioner"
    })
    tags = ["shifter", "gke", "provisioner"]

    # Exclusive placement (#1711 / #959): only provisioner Jobs and the
    # provisioner-launcher tolerate this taint, so no unrelated pod lands here
    # and takes a provisioner-pod-range alias IP. This is what lets the range
    # VPC scope management ingress to the provisioner pod range alone.
    taint {
      key    = "dedicated"
      value  = "provisioner"
      effect = "NO_SCHEDULE"
    }

    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}

resource "google_container_node_pool" "access" {
  name       = "${var.name_prefix}-access"
  project    = var.project_id
  location   = var.region
  cluster    = google_container_cluster.platform.name
  node_count = var.access_node_count

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  network_config {
    # Private nodes (no external IP): egress via Cloud NAT, matching every other
    # pool. The dedicated access pod range is the GCE firewall source identity
    # (#1711): portal + guacd pods scheduled here receive alias IPs from it, so
    # per-range participant ingress (SSH 22 / RDP 3389) is scoped to just these
    # access workloads instead of the broad platform pod range.
    enable_private_nodes = true
    create_pod_range     = false
    pod_range            = var.gke_access_pods_secondary_range_name
  }

  node_config {
    machine_type    = var.access_machine_type
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    # `role` is retained for observability, but the security-relevant nodeSelector
    # keys on node-restriction.kubernetes.io/shifter-pool: NodeRestriction forbids
    # a (compromised) kubelet from setting that prefix on its own Node, so a
    # rogue worker cannot self-label to attract portal/guacd pods (#1711 codex).
    labels = merge(var.common_labels, {
      role                                          = "access"
      "node-restriction.kubernetes.io/shifter-pool" = "access"
    })
    tags = ["shifter", "gke", "access"]

    # Exclusive placement (#1711): only portal + guacd tolerate this taint, so
    # the access pod range means "only the participant/operator access dialers".
    # A node label / selector alone is insufficient because any unspecialized
    # pod could otherwise schedule here and dilute the firewall source identity.
    taint {
      key    = "dedicated"
      value  = "access"
      effect = "NO_SCHEDULE"
    }

    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}
