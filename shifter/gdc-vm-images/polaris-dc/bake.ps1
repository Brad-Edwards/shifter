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

# Enable Windows SAC/EMS on serial (COM1). GDC Windows guests have no working
# network until the virtio NIC + DHCP come up and no VNC input channel, so when
# something goes wrong they sit at an unreachable black screen. SAC gives an
# operator command prompt over the KubeVirt bidirectional serial console with no
# network required. Takes effect on the next boot. (Consider disabling before
# the final golden export if serial-console access is a concern.)
try {
    bcdedit /ems "{current}" on | Out-Null
    bcdedit /emssettings EMSPORT:1 EMSBAUDRATE:115200 | Out-Null
    Write-Host "SAC/EMS enabled on COM1"
} catch { Write-Host "bcdedit ems error: $_" }

# Resolve this script's own directory (the answer cdrom) so we can copy the
# post-reboot script + a2_setup.ps1 to local disk (the cdrom letter can change
# across the reboot; local C: is stable).
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item (Join-Path $src "a2_setup.ps1") "C:\polaris\a2_setup.ps1" -Force

$phase = "C:\polaris\phase.txt"
$stage = if (Test-Path $phase) { Get-Content $phase -Raw } else { "promote" }

if ($stage.Trim() -eq "promote") {
    Set-Content -Path $phase -Value "seed" -Encoding ascii

    # NOTE: virtio boot (viostor) + network (NetKVM) drivers are loaded during
    # Windows Setup via the autounattend windowsPE <DriverPaths> (the documented
    # path: install on the virtio disk with viostor loaded in Setup). We do NOT
    # pnputil-install virtio drivers here: doing that post-install on a
    # SATA-booted OS leaves Windows without viostor marked boot-critical and
    # produces an INACCESSIBLE_BOOT_DEVICE boot loop on the next boot
    # (KubeVirt #16703 / RH BZ 1908421). Boot disk is virtio throughout.

    # Install the GDC guest agent so the DC's NIC gets its range-assigned IP.
    # On GDC, Windows has no cloud-init; the provisioner sets interfaces[].
    # ipAddresses and relies on the guest agent (attached as the "guest agent"
    # cdrom) to apply it. GDC's autoInstallGuestAgent runs the agent's
    # install.ps1 via a prepped-image hook that a raw ISO install bypasses, so
    # do it explicitly. install.ps1 registers a SYSTEM startup scheduled task
    # (guest-agent-launcher) that every boot copies the agent + the range's
    # SA/config from the attached agent disks and runs `guest-agent.exe
    # install/start`. The task persists in the golden image, so every range boot
    # re-installs/starts the agent against that range's own disks.
    $agentCd = (Get-Volume -FileSystemLabel "guest agent" -ErrorAction SilentlyContinue).DriveLetter
    if ($agentCd) {
        Write-Host "Installing GDC guest agent from ${agentCd}:\install.ps1"
        try { & "${agentCd}:\install.ps1" } catch { Write-Host "guest-agent install.ps1 error: $_" }
    } else {
        Write-Host "WARNING: GDC guest-agent cdrom (label 'guest agent') not found; NIC IP will not be applied"
    }

    # Install OpenSSH server for operator access to the DC (optional but handy).
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue | Out-Null
    Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue

    Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

    foreach ($feat in @("AD-Domain-Services", "DNS")) {
        if (-not (Get-WindowsFeature -Name $feat).Installed) {
            Install-WindowsFeature -Name $feat -IncludeManagementTools
        }
    }

    # Register the post-reboot seed phase as a SYSTEM AtStartup scheduled task.
    # RunOnce is unreliable here: after promotion the machine is a domain
    # controller and the local-Administrator autologon that RunOnce depends on
    # may not fire, so the seed would never run. A SYSTEM AtStartup task runs
    # regardless of interactive logon. The seed phase unregisters the task after
    # writing BAKE_DONE so it does not re-run in every range.
    Copy-Item $MyInvocation.MyCommand.Path "C:\polaris\bake.ps1" -Force
    $act = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\polaris\bake.ps1"'
    $trig = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName "PolarisBakeSeed" -Action $act -Trigger $trig `
        -User "NT AUTHORITY\SYSTEM" -RunLevel Highest -Force | Out-Null

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

# Install viostor + NetKVM as the LAST step, after all SATA reboots (install +
# promotion). The build installs on a SATA disk (WinPE sees SATA natively, so no
# viostor is needed during Setup); the exported golden disk is then attached on
# the virtio bus, and Windows boots on virtio with viostor as the boot driver
# (KubeVirt #16703 SATA-first-then-switch). Installing viostor and then rebooting
# ON SATA loops (INACCESSIBLE_BOOT_DEVICE), so this is the final action before a
# clean shutdown -- the VM never reboots on SATA after viostor is present.
$virtio = $null
foreach ($v in (Get-Volume | Where-Object { $_.DriveLetter })) {
    if (Test-Path ($v.DriveLetter + ":\viostor")) { $virtio = ($v.DriveLetter + ":"); break }
}
if ($virtio) {
    Write-Host "installing viostor + NetKVM from $virtio (last step, then shut down)"
    Get-ChildItem "$virtio\" -Recurse -Filter *.inf |
        Where-Object { $_.FullName -match '2k22' -and $_.FullName -match 'amd64' -and $_.FullName -match 'viostor|NetKVM' } |
        ForEach-Object { & pnputil.exe /add-driver $_.FullName /install 2>&1 | Out-Null }
} else {
    Write-Host "WARNING: virtio cdrom not found; the golden image will not boot on virtio"
}

Set-Content -Path "C:\polaris\BAKE_DONE" -Value (Get-Date -Format o) -Encoding ascii
# Unregister the seed task so it does not re-run when ranges boot the golden image.
Unregister-ScheduledTask -TaskName "PolarisBakeSeed" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "=== polaris-dc bake complete; BAKE_DONE written; shutting down for the SATA->virtio switch ==="
Stop-Transcript
Stop-Computer -Force
