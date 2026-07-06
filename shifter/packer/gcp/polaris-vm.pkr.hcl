// Polaris range-host (polaris-vm) GCE image.
//
// The Polaris participant endpoint is a Kali container inside this Ubuntu/
// Debian Docker host running the polaris docker-compose stack (17 containers
// incl. a14-kali, dns, a9-splice). Built on Google's GCE-native debian-12 base
// (same rationale as kali.pkr.hcl: the base is GCE-bootable with the guest
// agent), then layered with Docker Engine + compose, the Google Cloud SDK, and
// a host sshd moved to the management port so the Kali container can bind host
// :22 / :3389 for participant access.
//
// The full compose stack (docker-compose.yml + build context) is supplied at
// bake time the same way the AWS polaris-vm AMI is baked; the in-repo
// scenario tree under scenario-dev/polaris/build is staged into the image and
// host-setup.sh builds/pulls the stack when a compose file is present.
//
// Consumed by the GCE range-cell backend: the deploy points
// GCP_RANGE_KALI_IMAGE at this image family and the scenario's
// `ami_key: polaris-vm` selects the Kali/attacker profile
// (see gcp_range_cell_plan._host_access). The Windows DC uses the generic
// `dc` GCE image family (dc.pkr.hcl); boreas.local is promoted per-range by
// dc_setup, not baked.
source "googlecompute" "polaris-vm" {
  project_id              = var.project_id
  zone                    = var.zone
  machine_type            = var.machine_type
  source_image_family     = "debian-12"
  source_image_project_id = ["debian-cloud"]
  ssh_username            = "packer"

  // Docker Engine plus the multi-container polaris stack needs generous
  // headroom over the debian-12 base (10 GB).
  disk_size = 200

  network               = var.network
  subnetwork            = var.subnetwork
  service_account_email = var.service_account_email
  scopes                = ["https://www.googleapis.com/auth/cloud-platform"]

  use_internal_ip  = var.use_internal_ip
  omit_external_ip = var.use_internal_ip
  use_iap          = var.use_internal_ip

  image_name        = "${var.image_prefix}-polaris-vm-{{timestamp}}"
  image_family      = "${var.image_prefix}-polaris-vm"
  image_description = "Polaris range host: Debian Docker host running the polaris docker-compose stack (GCE)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "polaris-vm"
  }
}

build {
  sources = ["source.googlecompute.polaris-vm"]

  // Stage the in-repo polaris scenario build tree. The full compose stack is
  // supplied at bake time (as the AWS AMI is baked); whatever is present in
  // the repo is copied to a writable temp path first.
  provisioner "file" {
    source      = "../../../scenario-dev/polaris/build/"
    destination = "/tmp/polaris-build/"
  }

  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/polaris/scenario-dev/polaris",
      "sudo rm -rf /opt/polaris/scenario-dev/polaris/build",
      "sudo mv /tmp/polaris-build /opt/polaris/scenario-dev/polaris/build",
    ]
  }

  provisioner "shell" {
    scripts = [
      "scripts/polaris/host-setup.sh",
      "../scripts/common/cleanup.sh",
    ]
    execute_command = "sudo -S bash -c '{{ .Vars }} {{ .Path }}'"
  }

  post-processor "manifest" {
    output     = "polaris-vm-manifest.json"
    strip_path = true
  }
}
