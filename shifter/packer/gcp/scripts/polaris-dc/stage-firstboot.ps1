# POLARIS polaris-dc bake-time staging (GDC).
#
# Runs as a packer provisioner on the ephemeral builder BEFORE sysprep. The
# polaris-dc content (a2_setup.ps1) and the GDC promotion script
# (promote-boreas.ps1) have already been uploaded to C:\polaris\ by packer
# `file` provisioners. Here we install the AD DS + DNS features (so the golden
# image ships with them) and register a one-shot -AtStartup scheduled task that
# runs promote-boreas.ps1 on the first boot of the range VM.
#
# The forest is intentionally NOT promoted at bake time: GCESysprep cannot
# generalize a promoted domain controller. Capturing an un-promoted member
# server with AD DS installed + a first-boot promotion task is the supported
# pattern; promotion + content seed happen once when the range VM first boots.
$ErrorActionPreference = "Stop"
Write-Host "=== polaris-dc stage-firstboot $(Get-Date -Format o) ==="

if (-not (Test-Path "C:\polaris\promote-boreas.ps1")) {
    throw "C:\polaris\promote-boreas.ps1 missing (file provisioner did not run)"
}
if (-not (Test-Path "C:\polaris\a2_setup.ps1")) {
    throw "C:\polaris\a2_setup.ps1 missing (file provisioner did not run)"
}

foreach ($feat in @("AD-Domain-Services", "DNS")) {
    if (-not (Get-WindowsFeature -Name $feat).Installed) {
        Write-Host "  installing feature $feat"
        Install-WindowsFeature -Name $feat -IncludeManagementTools
    }
}

$taskName = "PolarisPromote"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\polaris\promote-boreas.ps1`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Write-Host "  registered first-boot task '$taskName' -> promote-boreas.ps1"

Write-Host "=== polaris-dc stage-firstboot complete ==="
