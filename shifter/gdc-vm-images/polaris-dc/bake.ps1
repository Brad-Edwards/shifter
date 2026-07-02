# polaris-dc bake (runs on the natively-installed GDC Windows guest).
#
# Invoked by the autounattend FirstLogonCommand after a clean Windows Server
# 2022 install (native on GDC VM Runtime, so UEFI/ESP/virtio are correct by
# construction). Promotes a BOREAS.LOCAL forest and seeds the polaris AD content
# via a2_setup.ps1 (staged next to this script on the answer cdrom). The
# promotion reboots, so the post-reboot phase (a2_setup) is registered as a
# RunOnce so it fires automatically after the reboot under the auto-logon admin.
#
# Writes C:\polaris\BAKE_DONE when finished so the build harness knows to stop
# the VM and export the golden boot disk.
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path C:\polaris | Out-Null
Start-Transcript -Path "C:\polaris\bake.log" -Append -Force
Write-Host "=== polaris-dc bake $(Get-Date -Format o) ==="

# Resolve this script's own directory (the answer cdrom) so we can copy the
# post-reboot script + a2_setup.ps1 to local disk (the cdrom letter can change
# across the reboot; local C: is stable).
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item (Join-Path $src "a2_setup.ps1") "C:\polaris\a2_setup.ps1" -Force

$phase = "C:\polaris\phase.txt"
$stage = if (Test-Path $phase) { Get-Content $phase -Raw } else { "promote" }

if ($stage.Trim() -eq "promote") {
    Set-Content -Path $phase -Value "seed" -Encoding ascii

    # Install OpenSSH server for operator access to the DC (optional but handy).
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue | Out-Null
    Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue

    Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

    foreach ($feat in @("AD-Domain-Services", "DNS")) {
        if (-not (Get-WindowsFeature -Name $feat).Installed) {
            Install-WindowsFeature -Name $feat -IncludeManagementTools
        }
    }

    # Register the post-reboot seed phase (this same script re-runs; phase=seed).
    $runonce = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"
    Set-ItemProperty -Path $runonce -Name "PolarisBakeSeed" `
        -Value ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $MyInvocation.MyCommand.Path.Replace($src, "C:\polaris") + '"')
    # Copy this script to C: so RunOnce can find it post-reboot.
    Copy-Item $MyInvocation.MyCommand.Path "C:\polaris\bake.ps1" -Force
    Set-ItemProperty -Path $runonce -Name "PolarisBakeSeed" `
        -Value 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\polaris\bake.ps1"'

    Import-Module ADDSDeployment
    $dsrm = ConvertTo-SecureString "DsrmR3store!2026" -AsPlainText -Force
    Write-Host "Install-ADDSForest BOREAS.LOCAL (reboots)..."
    Install-ADDSForest `
        -DomainName "boreas.local" -DomainNetbiosName "BOREAS" `
        -ForestMode "WinThreshold" -DomainMode "WinThreshold" -InstallDns `
        -DatabasePath "C:\Windows\NTDS" -LogPath "C:\Windows\NTDS" -SysvolPath "C:\Windows\SYSVOL" `
        -SafeModeAdministratorPassword $dsrm -CreateDnsDelegation:$false `
        -NoRebootOnCompletion:$false -Force:$true
    Stop-Transcript
    return
}

# phase == seed: AD DS is up after the promotion reboot; seed the content.
Write-Host "=== polaris-dc bake seed phase $(Get-Date -Format o) ==="
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try { Get-ADDomain -ErrorAction Stop | Out-Null; $ok = $true; break }
    catch { Write-Host "waiting for AD DS ($i)..."; Start-Sleep -Seconds 10 }
}
if (-not $ok) { throw "AD DS did not come up after promotion" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\polaris\a2_setup.ps1" -DnsForwarder "8.8.8.8"
if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { throw "a2_setup failed: $LASTEXITCODE" }

Set-Content -Path "C:\polaris\BAKE_DONE" -Value (Get-Date -Format o) -Encoding ascii
Write-Host "=== polaris-dc bake complete; BAKE_DONE written ==="
Stop-Transcript
