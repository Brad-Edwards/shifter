"""Tests for optional Caldera setup orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import caldera_setup
from orchestrators.setup_orchestrator import SetupError


def _instance(
    instance_id: str,
    *,
    role: str,
    os_type: str,
    private_ip: str,
    asset_type: str = "vm_runtime_vm",
) -> dict[str, str]:
    return {
        "uuid": instance_id,
        "instance_id": instance_id,
        "name": instance_id,
        "hostname": instance_id,
        "role": role,
        "os": os_type,
        "private_ip": private_ip,
        "asset_type": asset_type,
    }


def test_caldera_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    build_context = MagicMock()
    monkeypatch.setattr(caldera_setup, "build_guest_execution_context", build_context)

    caldera_setup.run_caldera_setup_if_enabled(
        [_instance("i-attacker", role="attacker", os_type="kali", private_ip="10.0.1.10")],
        {"caldera": {"enabled": False}},
    )

    build_context.assert_not_called()


def test_enabled_caldera_requires_a_kali_attacker() -> None:
    instances = [_instance("i-victim", role="victim", os_type="ubuntu", private_ip="10.0.1.20")]

    with pytest.raises(SetupError, match="exactly one Kali attacker"):
        caldera_setup.run_caldera_setup_if_enabled(instances, {"caldera": {"enabled": True}})


def test_enabled_caldera_rejects_multiple_attackers() -> None:
    instances = [
        _instance("i-a1", role="attacker", os_type="kali", private_ip="10.0.1.10"),
        _instance("i-a2", role="attacker", os_type="kali", private_ip="10.0.1.11"),
    ]

    with pytest.raises(SetupError, match="exactly one Kali attacker"):
        caldera_setup.run_caldera_setup_if_enabled(instances, {"caldera": {"enabled": True}})


def test_starts_server_before_deploying_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, object], str]] = []
    executions = []

    def fake_context(instance_data: dict[str, object], *, os_type: str | None = None, role: str | None = None):
        execution = SimpleNamespace(
            executor=object(),
            target=instance_data["instance_id"],
            document_name="AWS-RunPowerShellScript" if os_type == "windows" else "AWS-RunShellScript",
            transport_name="ssm",
            wait_for_ready=MagicMock(return_value=True),
            close=MagicMock(),
        )
        executions.append((instance_data, os_type, role, execution))
        return execution

    class FakeOrchestrator:
        def __init__(self, executor: object) -> None:
            self.executor = executor

        def orchestrate(self, target: str, plan: object, context: dict[str, object], *, document_name: str):
            calls.append((target, type(plan).__name__, context, document_name))
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(caldera_setup, "build_guest_execution_context", fake_context)
    monkeypatch.setattr(caldera_setup, "SetupOrchestrator", FakeOrchestrator)

    caldera_setup.run_caldera_setup_if_enabled(
        [
            _instance("i-attacker", role="attacker", os_type="kali", private_ip="10.0.1.10"),
            _instance("i-linux", role="victim", os_type="ubuntu", private_ip="10.0.1.20"),
            _instance("i-dc", role="dc", os_type="windows", private_ip="10.0.1.30"),
            _instance("pod-1", role="victim", os_type="ubuntu", private_ip="10.0.1.40", asset_type="scenario_pod"),
        ],
        {"caldera": {"enabled": True}},
    )

    assert [call[0] for call in calls] == ["i-attacker", "i-linux", "i-dc"]
    assert [call[1] for call in calls] == [
        "CalderaServerPlan",
        "LinuxSandcatAgentPlan",
        "WindowsSandcatAgentPlan",
    ]
    assert calls[1][2]["caldera_server_url"] == "http://10.0.1.10:8888"
    assert calls[2][2]["caldera_server_url"] == "http://10.0.1.10:8888"
    assert calls[2][3] == "AWS-RunPowerShellScript"
    assert all(execution.close.called for *_prefix, execution in executions)


def test_enabled_caldera_honors_callback_port_and_target_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_context(instance_data: dict[str, object], *, os_type: str | None = None, role: str | None = None):
        return SimpleNamespace(
            executor=object(),
            target=instance_data["instance_id"],
            document_name="AWS-RunPowerShellScript" if os_type == "windows" else "AWS-RunShellScript",
            transport_name="ssm",
            wait_for_ready=MagicMock(return_value=True),
            close=MagicMock(),
        )

    class FakeOrchestrator:
        def __init__(self, executor: object) -> None:
            self.executor = executor

        def orchestrate(self, target: str, plan: object, context: dict[str, object], *, document_name: str):
            calls.append((target, type(plan).__name__, context))
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(caldera_setup, "build_guest_execution_context", fake_context)
    monkeypatch.setattr(caldera_setup, "SetupOrchestrator", FakeOrchestrator)

    caldera_setup.run_caldera_setup_if_enabled(
        [
            _instance("i-attacker", role="attacker", os_type="kali", private_ip="10.0.1.10"),
            _instance("i-linux", role="victim", os_type="ubuntu", private_ip="10.0.1.20"),
            _instance("i-dc", role="dc", os_type="windows", private_ip="10.0.1.30"),
        ],
        {"caldera": {"enabled": True, "callback_port": 9999, "target_roles": ["victim"]}},
    )

    assert [call[0] for call in calls] == ["i-attacker", "i-linux"]
    assert calls[0][2]["callback_port"] == 9999
    assert calls[1][2]["caldera_server_url"] == "http://10.0.1.10:9999"


def test_run_instance_setup_invokes_caldera_after_standard_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    captured: dict[str, object] = {}

    import instance_orchestrator

    monkeypatch.setattr(
        instance_orchestrator,
        "_setup_dc_instances_blocking",
        lambda *_args, **_kwargs: calls.append("dc"),
    )
    monkeypatch.setattr(
        instance_orchestrator,
        "_setup_other_instances_parallel",
        lambda *_args, **_kwargs: calls.append("other"),
    )

    def fake_caldera(instances_output: list[dict[str, object]], range_spec: dict[str, object]) -> None:
        calls.append("caldera")
        captured["instances_output"] = instances_output
        captured["range_spec"] = range_spec

    monkeypatch.setattr(instance_orchestrator, "run_caldera_setup_if_enabled", fake_caldera)

    instances_output = [_instance("i-attacker", role="attacker", os_type="kali", private_ip="10.0.1.10")]
    range_spec = {"caldera": {"enabled": True}, "subnets": []}

    instance_orchestrator.run_instance_setup(instances_output, range_spec)

    assert calls == ["dc", "other", "caldera"]
    assert captured["instances_output"] == instances_output
    assert captured["range_spec"] is range_spec
