// Kali Linux Rolling GCE image (kali-linux-headless, sshpass, Caldera, Claude
// Code). GCP has NO public Kali image project (the AWS path uses an AWS
// Marketplace product code), so this builder consumes an operator-imported
// image supplied via var.kali_source_image. Import one first, e.g.:
//   gcloud compute images import shifter-kali-base \
//     --source-file=gs://<bucket>/kali-rolling.tar.gz --os=debian-11
// then pass its name/self-link as kali_source_image at build time. The build
// fails loud if the variable is empty.
source "googlecompute" "kali" {
  project_id   = var.project_id
  zone         = var.zone
  machine_type = var.machine_type
  source_image = var.kali_source_image
  ssh_username = "kali"

  network               = var.network
  subnetwork            = var.subnetwork
  service_account_email = var.service_account_email
  scopes                = ["https://www.googleapis.com/auth/cloud-platform"]

  use_internal_ip  = var.use_internal_ip
  omit_external_ip = var.use_internal_ip
  use_iap          = var.use_internal_ip

  image_name        = "${var.image_prefix}-kali-{{timestamp}}"
  image_family      = "${var.image_prefix}-kali"
  image_description = "Kali Linux Rolling with kali-linux-headless, sshpass, Caldera, Claude Code (GCE)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "kali"
  }
}

build {
  sources = ["source.googlecompute.kali"]

  provisioner "shell" {
    scripts = [
      "../scripts/kali/base.sh",
      "../scripts/kali/tools.sh",
      "../scripts/kali/caldera.sh",
      "../scripts/kali/claude-code.sh",
      "../scripts/common/cleanup.sh",
    ]
    execute_command = "sudo -S bash -c '{{ .Vars }} {{ .Path }}'"
  }

  post-processor "manifest" {
    output     = "kali-manifest.json"
    strip_path = true
  }
}
