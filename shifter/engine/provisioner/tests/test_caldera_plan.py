"""Tests for optional Caldera runtime setup plans."""

from __future__ import annotations

import pytest

from plans.caldera import CalderaServerPlan, LinuxSandcatAgentPlan, WindowsSandcatAgentPlan


class TestCalderaServerPlan:
    def test_starts_baked_caldera_install(self) -> None:
        plan = CalderaServerPlan()

        assert [step.name for step in plan.steps] == ["start_caldera_server"]
        script = plan.steps[0].script
        assert "/opt/caldera" in script
        assert "/usr/local/bin/start-caldera" in script
        assert "{{ callback_port }}" in script
        assert "conf/default.yml" in script
        assert "systemctl enable --now caldera.service" in script
        assert "ExecStart=$start_command --host" not in script
        assert 'nohup "$start_command" --host' not in script

    def test_verifies_local_caldera_http_endpoint(self) -> None:
        plan = CalderaServerPlan()

        assert plan.verify_step is not None
        assert plan.verify_step.name == "verify_caldera_server"
        assert plan.verify_step.is_verification is True
        assert "http://127.0.0.1:{{ callback_port }}" in plan.verify_step.script

    def test_context_defaults_to_port_8888_and_baked_paths(self) -> None:
        context = CalderaServerPlan().get_context({})

        assert context == {
            "callback_port": 8888,
            "caldera_working_directory": "/opt/caldera",
            "caldera_start_command": "/usr/local/bin/start-caldera",
        }


class TestLinuxSandcatAgentPlan:
    def test_downloads_and_launches_linux_sandcat_from_caldera(self) -> None:
        plan = LinuxSandcatAgentPlan()
        script = "\n".join(step.script for step in plan.steps)

        assert "/tmp/sandcat.go-linux" in script  # noqa: S108 - verifies issue-mandated guest payload path.
        assert '-H "file:sandcat.go"' in script
        assert '-H "platform:linux"' in script
        assert "{{ caldera_server_url }}/file/download" in script
        assert '-server "$server_url"' in script
        assert "rm -f /tmp/sandcat.go-linux" not in script

    def test_context_requires_server_url(self) -> None:
        plan = LinuxSandcatAgentPlan()

        context = plan.get_context({"caldera_server_url": "http://10.0.1.10:8888"})
        assert context["caldera_server_url"] == "http://10.0.1.10:8888"
        with pytest.raises(ValueError, match="caldera_server_url"):
            plan.get_context({})


class TestWindowsSandcatAgentPlan:
    def test_downloads_and_launches_windows_sandcat_with_defender_exclusion(self) -> None:
        plan = WindowsSandcatAgentPlan()
        script = "\n".join(step.script for step in plan.steps)

        assert "C:\\Users\\Public\\sandcat.exe" in script
        assert "Add-MpPreference" in script
        assert "Set-MpPreference" in script
        assert '$webClient.Headers.Add("file", "sandcat.go")' in script
        assert '$webClient.Headers.Add("platform", "windows")' in script
        assert "{{ caldera_server_url }}/file/download" in script
        assert "-server" in script
        assert "{{ caldera_server_url }}" in script
        assert "Remove-Item $payloadPath" not in script

    def test_context_requires_server_url(self) -> None:
        plan = WindowsSandcatAgentPlan()

        context = plan.get_context({"caldera_server_url": "http://10.0.1.10:8888"})
        assert context["caldera_server_url"] == "http://10.0.1.10:8888"
        assert context["windows_defender_mode"] == "path_exclusion"
        with pytest.raises(ValueError, match="caldera_server_url"):
            plan.get_context({"caldera_server_url": ""})
