// TechVault native GCE range host: Ubuntu 24.04 with the pinned APTL
// techvault-operational stack, UID-1000 participant seat, xrdp, VS Code, and
// Claude Code. The compose stack is captured running so its restart policies
// restore the participant environment on every clean boot.
source "googlecompute" "techvault" {
  project_id              = var.project_id
  zone                    = var.zone
  machine_type            = var.machine_type
  source_image_family     = "ubuntu-2404-lts-amd64"
  source_image_project_id = ["ubuntu-os-cloud"]
  ssh_username            = "ubuntu"

  // The operational stack and local images exceed the generic Kali profile.
  disk_size = 150

  network               = var.network
  subnetwork            = var.subnetwork
  service_account_email = var.service_account_email
  scopes                = ["https://www.googleapis.com/auth/cloud-platform"]

  use_internal_ip  = var.use_internal_ip
  omit_external_ip = var.use_internal_ip
  use_iap          = var.use_internal_ip

  image_name        = "${var.image_prefix}-techvault-{{timestamp}}"
  image_family      = "${var.image_prefix}-techvault"
  image_description = "TechVault APTL operational stack and participant seat (GCE)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "techvault"
  }
}

build {
  sources = ["source.googlecompute.techvault"]

  provisioner "file" {
    source      = "../scripts/techvault/aptl-requirements.lock"
    destination = "/tmp/aptl-requirements.lock"
  }

  provisioner "shell" {
    environment_vars = ["APTL_REQUIREMENTS_LOCK=/tmp/aptl-requirements.lock"]
    scripts = [
      "../scripts/techvault/toolchain.sh",
      "../scripts/techvault/stack.sh",
      "../scripts/techvault/seat.sh",
      "../scripts/techvault/wait-stack.sh",
    ]
    execute_command = "sudo -S bash -c '{{ .Vars }} {{ .Path }}'"
  }

  post-processor "manifest" {
    output     = "techvault-manifest.json"
    strip_path = true
  }
}
