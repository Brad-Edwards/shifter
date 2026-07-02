// Shared build-time bootstrap for the Windows GCE builders (windows + dc).
//
// Single source of truth for the WinRM-over-TLS bootstrap so both Windows
// templates harden identically (#505 codex security review): an HTTPS WinRM
// listener on 5986 bound to a self-signed cert, Basic-over-HTTP disabled, and
// only 5986 opened on the firewall. The transient local admin (packer_user)
// and this listener live on the ephemeral builder VM only; GCESysprep removes
// them before the image is captured, so the bootstrap password never persists
// into the published image and never crosses the wire in cleartext.
locals {
  winrm_https_bootstrap_ps1 = <<-EOT
    $ErrorActionPreference = 'Stop'
    $pw = ConvertTo-SecureString '${var.winrm_bootstrap_password}' -AsPlainText -Force
    New-LocalUser -Name 'packer_user' -Password $pw -PasswordNeverExpires -AccountNeverExpires
    Add-LocalGroupMember -Group 'Administrators' -Member 'packer_user'

    winrm quickconfig -quiet
    # Disable plaintext transport and HTTP Basic; require an encrypted channel.
    winrm set winrm/config/service '@{AllowUnencrypted="false"}'
    winrm set winrm/config/service/auth '@{Basic="false"}'

    # Self-signed cert for the ephemeral builder; bind an HTTPS listener on 5986
    # and remove any plaintext HTTP listener that quickconfig created.
    $cert = New-SelfSignedCertificate -DnsName ([System.Net.Dns]::GetHostName()) -CertStoreLocation Cert:\LocalMachine\My
    $thumb = $cert.Thumbprint
    Remove-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTPS"} -ErrorAction SilentlyContinue
    New-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTPS"} -ValueSet @{Hostname=([System.Net.Dns]::GetHostName());CertificateThumbprint=$thumb}
    Remove-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTP"} -ErrorAction SilentlyContinue

    netsh advfirewall firewall add rule name="WinRM-HTTPS-5986" protocol=TCP dir=in localport=5986 action=allow
    netsh advfirewall firewall delete rule name="WinRM-5985" protocol=TCP localport=5985
  EOT

  # DC-specific bootstrap: the polaris-dc build promotes a BOREAS.LOCAL forest at
  # bake time, which reboots and removes local accounts (only the built-in
  # Administrator survives as the domain Administrator). So this bootstrap
  # configures WinRM on the BUILT-IN Administrator (not a throwaway packer_user):
  # its password is preserved across promotion, so packer's WinRM reconnect after
  # the promotion reboot authenticates as the domain Administrator with the same
  # password. The image is captured un-sysprepped (a promoted DC cannot be
  # generalized), so the build password is overwritten by the CTF Administrator
  # password (a2_setup.ps1) as the final provisioner before capture.
  winrm_https_bootstrap_dc_ps1 = <<-EOT
    $ErrorActionPreference = 'Stop'
    # Use the built-in Administrator (net user is bulletproof vs Set-LocalUser on
    # a possibly-disabled account) so the identity survives the promotion reboot
    # as the domain Administrator. The source builder is launched with
    # disable-account-manager=true so the GCE guest agent does not reset this
    # password out from under packer's WinRM connection.
    net user Administrator '${var.winrm_bootstrap_password}' /active:yes

    winrm quickconfig -quiet
    winrm set winrm/config/service '@{AllowUnencrypted="false"}'
    winrm set winrm/config/service/auth '@{Basic="false"}'

    $cert = New-SelfSignedCertificate -DnsName ([System.Net.Dns]::GetHostName()) -CertStoreLocation Cert:\LocalMachine\My
    $thumb = $cert.Thumbprint
    Remove-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTPS"} -ErrorAction SilentlyContinue
    New-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTPS"} -ValueSet @{Hostname=([System.Net.Dns]::GetHostName());CertificateThumbprint=$thumb}
    Remove-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address="*";Transport="HTTP"} -ErrorAction SilentlyContinue

    netsh advfirewall firewall add rule name="WinRM-HTTPS-5986" protocol=TCP dir=in localport=5986 action=allow
    netsh advfirewall firewall delete rule name="WinRM-5985" protocol=TCP localport=5985
  EOT
}
