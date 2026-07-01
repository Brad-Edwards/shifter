# POLARIS polaris-dc first-boot promotion (GDC).
#
# Ports scripts/polaris-aws-range/a2_install_adds.ps1 to the GDC bake model:
# instead of an operator SSM Run Command against a running AMI, this runs from
# the `PolarisPromote` scheduled task on the FIRST boot of the range VM (the
# stage-firstboot.ps1 packer provisioner registered that task and the AD DS/DNS
# features are already installed in the image). It promotes a fresh BOREAS.LOCAL
# forest and schedules the post-reboot content seed (a2_setup.ps1, staged
# locally at bake time — no S3 fetch). The DC content is range-independent, so
# the promoted DC is identical for every range.
#
# Promotion is deferred to range boot (not baked promoted) on purpose: sysprep /
# GCESysprep does not support a promoted domain controller, so the image is
# captured as an un-promoted member server with AD DS installed, and the forest
# is created here at first boot.
[CmdletBinding()]
param(
    [string]$DsrmPassword     = "DsrmR3store!2026",
    [string]$AdminPassword    = "CortexSavesTheDay!",
    [string]$SetupScriptLocal = "C:\polaris\a2_setup.ps1",
    # GDC has no 169.254.169.253 link-local resolver; forward external queries
    # to a routable public resolver.
    [string]$DnsForwarder     = "8.8.8.8"
)

$ErrorActionPreference = "Stop"
Start-Transcript -Path "C:\polaris-promote.log" -Append -Force
Write-Host "=== POLARIS polaris-dc promote $(Get-Date -Format o) ==="

# One-shot: drop the boot task that launched us so it never re-fires.
Unregister-ScheduledTask -TaskName "PolarisPromote" -Confirm:$false -ErrorAction SilentlyContinue

Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False
net user Administrator $AdminPassword | Out-Null

# Features are already present in the baked image, but assert them (idempotent).
foreach ($feat in @("AD-Domain-Services", "DNS")) {
    if (-not (Get-WindowsFeature -Name $feat).Installed) {
        Install-WindowsFeature -Name $feat -IncludeManagementTools
    }
}

# Register the post-promotion content seed to run once at the next boot (after
# Install-ADDSForest reboots) under SYSTEM, before any interactive logon.
$taskName = "PolarisA2Setup"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"$SetupScriptLocal`" " +
               "-AdminPassword `"$AdminPassword`" -DnsForwarder `"$DnsForwarder`"")
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Write-Host "  scheduled '$taskName' for post-promotion boot"

if ($env:COMPUTERNAME -ne "DC01") {
    Rename-Computer -NewName "dc01" -Force
    Write-Host "  computer rename queued (dc01)"
}

Import-Module ADDSDeployment
$secureDsrm = ConvertTo-SecureString $DsrmPassword -AsPlainText -Force
Write-Host "  Install-ADDSForest BOREAS.LOCAL (this reboots)..."
Install-ADDSForest `
    -DomainName "boreas.local" `
    -DomainNetbiosName "BOREAS" `
    -ForestMode "WinThreshold" `
    -DomainMode "WinThreshold" `
    -InstallDns `
    -DatabasePath "C:\Windows\NTDS" `
    -LogPath "C:\Windows\NTDS" `
    -SysvolPath "C:\Windows\SYSVOL" `
    -SafeModeAdministratorPassword $secureDsrm `
    -CreateDnsDelegation:$false `
    -NoRebootOnCompletion:$false `
    -Force:$true

Write-Host "=== Install-ADDSForest returned without reboot — check manually ==="
Stop-Transcript
