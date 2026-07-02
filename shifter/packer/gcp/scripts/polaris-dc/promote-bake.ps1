# POLARIS polaris-dc bake-time promotion (GDC pre-promoted DC).
#
# Runs on the packer builder over WinRM. Installs AD DS + DNS, disables the
# firewall (the CTF DC serves LDAP/Kerberos/SMB/GC to the range), sets the
# BOREAS.LOCAL DNS forwarder, renames to dc01, and promotes a fresh forest with
# -NoRebootOnCompletion so packer controls the reboot (a `windows-restart`
# provisioner follows). After the reboot the builder comes back as the domain
# controller; the WinRM reconnect authenticates as the domain Administrator
# (same password as the built-in Administrator set in the DC WinRM bootstrap),
# and finalize.ps1 seeds the AD content.
#
# Promotion is done at bake time (not first boot) so every range boots an
# already-promoted DC — fast spin-up, no per-range 20-minute promotion. The
# image is captured un-sysprepped because GCESysprep cannot generalize a
# promoted domain controller (and on GDC there is no metadata server to answer
# a sysprepped image's OOBE, which is why the earlier sysprepped build hung with
# no network).
[CmdletBinding()]
param(
    [string]$DsrmPassword = "DsrmR3store!2026",
    [string]$DnsForwarder = "8.8.8.8"
)
$ErrorActionPreference = "Stop"
Start-Transcript -Path "C:\polaris-promote-bake.log" -Append -Force
Write-Host "=== polaris-dc promote-bake $(Get-Date -Format o) ==="

# The DC serves AD to the whole range; the CTF design runs it firewall-off
# (a2_setup assumes reachable LDAP/SMB/etc.).
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

foreach ($feat in @("AD-Domain-Services", "DNS")) {
    if (-not (Get-WindowsFeature -Name $feat).Installed) {
        Write-Host "  installing feature $feat"
        Install-WindowsFeature -Name $feat -IncludeManagementTools
    }
}

if ($env:COMPUTERNAME -ne "DC01") {
    Rename-Computer -NewName "dc01" -Force
    Write-Host "  computer rename queued (dc01)"
}

Import-Module ADDSDeployment
$secureDsrm = ConvertTo-SecureString $DsrmPassword -AsPlainText -Force
Write-Host "  Install-ADDSForest BOREAS.LOCAL (reboot deferred to packer)..."
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
    -NoRebootOnCompletion:$true `
    -Force:$true

# Stash the forwarder for finalize.ps1 (runs post-reboot, once DNS is serving).
Set-Content -Path "C:\polaris-dns-forwarder.txt" -Value $DnsForwarder -Encoding ascii
Write-Host "=== promote-bake complete (packer will reboot next) ==="
Stop-Transcript
