# POLARIS polaris-dc bake-time finalize (runs after the promotion reboot).
#
# The builder has rebooted as the BOREAS.LOCAL domain controller and packer has
# reconnected over WinRM as the domain Administrator. Wait for AD DS to serve,
# then run the AD content seed (a2_setup.ps1, staged at C:\polaris\a2_setup.ps1)
# which creates the OUs/users/groups/SPNs/DCSync ACL/flags/shares and sets the
# CTF Administrator password. This is the last provisioner before capture, so
# a2_setup's Administrator-password change does not break any later WinRM step.
$ErrorActionPreference = "Stop"
Start-Transcript -Path "C:\polaris-finalize.log" -Append -Force
Write-Host "=== polaris-dc finalize $(Get-Date -Format o) ==="

# Firewall stays off on the DC (assert again post-reboot).
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# Wait until AD Web Services / NTDS answer.
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try { Get-ADDomain -ErrorAction Stop | Out-Null; $ok = $true; break }
    catch { Write-Host "waiting for AD DS ($i)..."; Start-Sleep -Seconds 10 }
}
if (-not $ok) { throw "AD DS did not become available after promotion" }
Write-Host "AD DS is serving."

$fwd = "8.8.8.8"
if (Test-Path "C:\polaris-dns-forwarder.txt") {
    $fwd = (Get-Content "C:\polaris-dns-forwarder.txt" -Raw).Trim()
}

if (-not (Test-Path "C:\polaris\a2_setup.ps1")) {
    throw "C:\polaris\a2_setup.ps1 missing (file provisioner did not run)"
}
Write-Host "Running a2_setup.ps1 (DNS forwarder $fwd)..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\polaris\a2_setup.ps1" -DnsForwarder $fwd
if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
    throw "a2_setup.ps1 failed with exit code $LASTEXITCODE"
}
Write-Host "=== polaris-dc finalize complete ==="
Stop-Transcript
