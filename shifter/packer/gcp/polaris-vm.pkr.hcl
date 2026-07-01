// POLARIS polaris-vm GDC image: an Ubuntu 22.04 docker host carrying the baked
// 17-service NORTHSTORM compose stack (a14-kali is a container, so the host OS
// is Ubuntu, not Kali — matching the AWS polaris-vm golden AMI, which is built
// on the shifter-ubuntu base). The compose stack + per-asset Dockerfiles + flag
// content are fetched from a private GCS build tarball at bake time (they carry
// CTF answers and are kept out of source control, mirroring the AWS S3 bake
// tarball consumed by polaris-scenario-bake.yml / issue #618).
//
// The generic kali/ubuntu/windows/dc builders in this directory are unaffected;
// Packer evaluates every *.pkr.hcl here as one config but each `build` block is
// selected by -only=googlecompute.<name>.
source "googlecompute" "polaris-vm" {
  project_id   = var.project_id
  zone         = var.zone
  machine_type = var.machine_type

  source_image_family     = "ubuntu-2204-lts"
  source_image_project_id = ["ubuntu-os-cloud"]
  ssh_username            = "packer"

  // The baked stack ships 17 built images (some heavy: gitea, postgres, a
  // Kali XFCE desktop container) plus the Ubuntu base, so size the boot disk
  // well above the generic ubuntu builder. The exported qcow2 virtual size
  // sets the floor for GDC_POLARIS_VM_DISK_SIZE_GIB.
  disk_size = 60

  network               = var.network
  subnetwork            = var.subnetwork
  service_account_email = var.service_account_email
  scopes                = ["https://www.googleapis.com/auth/cloud-platform"]

  use_internal_ip  = var.use_internal_ip
  omit_external_ip = var.use_internal_ip
  use_iap          = var.use_internal_ip

  image_name        = "${var.image_prefix}-polaris-vm-{{timestamp}}"
  image_family      = "${var.image_prefix}-polaris-vm"
  image_description = "POLARIS polaris-vm: Ubuntu docker host with the baked NORTHSTORM 17-service compose stack (GCE)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "polaris-vm"
  }
}

build {
  sources = ["source.googlecompute.polaris-vm"]

  provisioner "shell" {
    scripts = [
      "scripts/polaris-vm/build-stack.sh",
      # GCP-only: force cloud-init's NoCloud datasource so GDC VM Runtime
      # guests consume the range userData. Runs last so it is the final state
      # captured into the image.
      "scripts/gdc-cloudinit-datasource.sh",
    ]
    environment_vars = [
      "POLARIS_BUILD_TARBALL_URI=${var.polaris_build_tarball_uri}",
    ]
    execute_command = "sudo -S bash -c '{{ .Vars }} {{ .Path }}'"
  }

  post-processor "manifest" {
    output     = "polaris-vm-manifest.json"
    strip_path = true
  }
}
