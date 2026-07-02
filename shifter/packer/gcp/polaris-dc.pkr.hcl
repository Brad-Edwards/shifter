// POLARIS polaris-dc GDC image: a PRE-PROMOTED Windows Server 2022 BOREAS.LOCAL
// domain controller with the polaris AD content baked in. Every range boots an
// already-promoted DC (fast spin-up, no per-range promotion).
//
// This is captured UN-SYSPREPPED on purpose: GCESysprep cannot generalize a
// promoted DC, and on GDC (no GCE metadata server) a sysprepped image hangs at
// OOBE with no network. Un-sysprepped, the specialized image boots straight to
// the DC. WinRM is bootstrapped on the built-in Administrator (locals.pkr.hcl
// winrm_https_bootstrap_dc_ps1) so the connection survives the promotion reboot
// (the built-in Administrator becomes the domain Administrator, same password).
// virtio-win drivers are staged so the guest binds GDC's virtio NIC (the GCE
// image ships gVNIC, not virtio-net — the cause of the DC having no network).
source "googlecompute" "polaris-dc" {
  project_id              = var.project_id
  zone                    = var.zone
  machine_type            = var.machine_type
  source_image_family     = "windows-2022"
  source_image_project_id = ["windows-cloud"]
  disk_size               = 100

  communicator   = "winrm"
  winrm_username = "Administrator"
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
    windows-startup-script-ps1 = local.winrm_https_bootstrap_dc_ps1
    // Stop the GCE guest agent from resetting the built-in Administrator
    // password that the bootstrap sets (and that packer reconnects with after
    // the promotion reboot). Without this the agent races the bootstrap and
    // WinRM auth fails.
    disable-account-manager = "true"
  }

  image_name        = "${var.image_prefix}-polaris-dc-{{timestamp}}"
  image_family      = "${var.image_prefix}-polaris-dc"
  image_description = "POLARIS polaris-dc: pre-promoted BOREAS.LOCAL DC with polaris AD content (GCE, un-sysprepped)"
  image_labels = {
    project    = "shifter"
    managed-by = "packer"
    image-type = "polaris-dc"
  }
}

build {
  sources = ["source.googlecompute.polaris-dc"]

  // Base system configuration (RDP, firewall, WinRM) in the dc role.
  provisioner "powershell" {
    environment_vars = ["PACKER_ROLE=dc"]
    script           = "../scripts/windows/base.ps1"
  }

  // OpenSSH (harmless; useful for operator access to the DC).
  provisioner "powershell" {
    elevated_user     = "Administrator"
    elevated_password = var.winrm_bootstrap_password
    environment_vars  = ["PACKER_ROLE=dc"]
    script            = "../scripts/windows/services.ps1"
  }

  // Stage upstream virtio-win drivers so the guest binds GDC's virtio NIC.
  provisioner "powershell" {
    script = "scripts/polaris-dc/install-virtio.ps1"
  }

  // Stage the AD content seed for finalize.ps1 to run post-promotion.
  provisioner "powershell" {
    inline = ["New-Item -ItemType Directory -Force -Path C:\\polaris | Out-Null"]
  }
  provisioner "file" {
    source      = "../../../scripts/polaris-aws-range/a2_setup.ps1"
    destination = "C:\\polaris\\a2_setup.ps1"
  }

  // Install AD DS/DNS, set forwarder + firewall-off, rename to dc01, and
  // Install-ADDSForest with the reboot deferred to the windows-restart below.
  provisioner "powershell" {
    script = "scripts/polaris-dc/promote-bake.ps1"
  }

  // Apply the deferred promotion reboot; packer reconnects over WinRM as the
  // domain Administrator once the DC is back up.
  provisioner "windows-restart" {
    restart_timeout = "20m"
  }

  // Post-reboot: wait for AD DS, seed the polaris AD content (a2_setup.ps1).
  // MUST BE LAST — a2_setup sets the CTF Administrator password.
  provisioner "powershell" {
    elevated_user     = "Administrator"
    elevated_password = var.winrm_bootstrap_password
    script            = "scripts/polaris-dc/finalize.ps1"
  }

  post-processor "manifest" {
    output     = "polaris-dc-manifest.json"
    strip_path = true
  }
}
