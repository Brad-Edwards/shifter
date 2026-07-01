// POLARIS polaris-dc GDC image: Windows Server 2022 with the AD DS + DNS
// features installed and a first-boot task that promotes a fresh BOREAS.LOCAL
// forest and seeds the polaris AD content (users/groups/SPNs/DCSync ACL/flags/
// shares). Mirrors the AWS polaris-dc golden AMI, but the promotion is deferred
// to the range VM's first boot because GCESysprep cannot generalize a promoted
// domain controller — so the image is captured as an un-promoted member server
// with the promotion staged.
//
// Shares the WinRM-over-TLS bootstrap + sysprep flow with dc.pkr.hcl /
// windows.pkr.hcl (locals.pkr.hcl). The AD content script is the single
// source of truth scripts/polaris-aws-range/a2_setup.ps1 (parameterized DNS
// forwarder); the GDC-specific promotion wrapper is scripts/polaris-dc/.
source "googlecompute" "polaris-dc" {
  project_id              = var.project_id
  zone                    = var.zone
  machine_type            = var.machine_type
  source_image_family     = "windows-2022"
  source_image_project_id = ["windows-cloud"]
  disk_size               = 100

  communicator   = "winrm"
  winrm_username = "packer_user"
  winrm_password = var.winrm_bootstrap_password
  winrm_insecure = true
  winrm_use_ssl  = true
  winrm_use_ntlm = true
  winrm_timeout  = "30m"

  network               = var.network
  subnetwork            = var.subnetwork
  service_account_email = var.service_account_email
  scopes                = ["https://www.googleapis.com/auth/cloud-platform"]

  use_internal_ip  = var.use_internal_ip
  omit_external_ip = var.use_internal_ip
  use_iap          = var.use_internal_ip

  metadata = {
    windows-startup-script-ps1 = local.winrm_https_bootstrap_ps1
  }

  image_name        = "${var.image_prefix}-polaris-dc-{{timestamp}}"
  image_family      = "${var.image_prefix}-polaris-dc"
  image_description = "POLARIS polaris-dc: Windows Server 2022, first-boot BOREAS.LOCAL forest + polaris AD content (GCE)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "polaris-dc"
  }
}

build {
  sources = ["source.googlecompute.polaris-dc"]

  // Base system configuration (RDP, firewall, WinRM, AD DS feature via dc role)
  provisioner "powershell" {
    environment_vars = ["PACKER_ROLE=dc"]
    script           = "../scripts/windows/base.ps1"
  }

  // OpenSSH so the GDC range setup-runner can reach the DC
  provisioner "powershell" {
    elevated_user     = "packer_user"
    elevated_password = var.winrm_bootstrap_password
    environment_vars  = ["PACKER_ROLE=dc"]
    script            = "../scripts/windows/services.ps1"
  }

  // Stage the polaris content + promotion scripts under C:\polaris.
  provisioner "powershell" {
    inline = ["New-Item -ItemType Directory -Force -Path C:\\polaris | Out-Null"]
  }
  provisioner "file" {
    source      = "../../../scripts/polaris-aws-range/a2_setup.ps1"
    destination = "C:\\polaris\\a2_setup.ps1"
  }
  provisioner "file" {
    source      = "scripts/polaris-dc/promote-boreas.ps1"
    destination = "C:\\polaris\\promote-boreas.ps1"
  }

  // Install AD DS/DNS features + register the first-boot promotion task.
  provisioner "powershell" {
    script = "scripts/polaris-dc/stage-firstboot.ps1"
  }

  // GCESysprep (MUST BE LAST) — generalizes the un-promoted member server.
  provisioner "powershell" {
    script = "scripts/windows/sysprep.ps1"
  }

  post-processor "manifest" {
    output     = "polaris-dc-manifest.json"
    strip_path = true
  }
}
