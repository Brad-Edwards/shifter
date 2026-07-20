"""AWS guest-password transport tests (#1790)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import instance_setup
from orchestrators.setup_orchestrator import SetupError


def _execution(*, stdout: str):
    executor = MagicMock()
    executor.run_command.return_value = SimpleNamespace(success=True, stdout=stdout)
    return instance_setup.GuestExecutionContext(
        executor=executor,
        target="i-0123456789",
        document_name="AWS-RunPowerShellScript",
        transport_name="ssm",
    )


def test_reads_and_normalizes_ssh_host_key_over_ssm():
    execution = _execution(stdout="noise\nSHIFTER_SSH_HOST_KEY=ssh-ed25519 AAAATEST guest@host\n")

    result = instance_setup._read_aws_ssh_host_key_or_raise(
        execution,
        platform="windows",
        failure_prefix="password push failed",
    )

    assert result == "ssh-ed25519 AAAATEST"
    execution.executor.run_command.assert_called_once_with(
        execution.target,
        instance_setup._WINDOWS_SSH_HOST_KEY_SCRIPT,
        timeout_seconds=60,
        document_name=execution.document_name,
    )


def test_rejects_missing_or_non_ed25519_ssh_host_key():
    execution = _execution(stdout="SHIFTER_SSH_HOST_KEY=ssh-rsa AAAATEST\n")

    with pytest.raises(SetupError, match="valid ed25519 SSH host key"):
        instance_setup._read_aws_ssh_host_key_or_raise(
            execution,
            platform="windows",
            failure_prefix="password push failed",
        )


def test_aws_password_push_uses_secret_value_on_separate_ssh_execution(monkeypatch):
    original_execution = _execution(stdout="unused")
    password_execution = instance_setup.GuestExecutionContext(
        executor=MagicMock(),
        target="10.20.30.40",
        document_name="AWS-RunPowerShellScript",
        transport_name="pinned-ssh",
    )
    close = MagicMock()
    password_execution.close = close  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    monkeypatch.setattr(instance_setup, "_get_cloud_provider", lambda: "aws")
    monkeypatch.setattr(
        instance_setup,
        "_build_aws_password_execution_or_raise",
        lambda *_args, **_kwargs: password_execution,
    )
    monkeypatch.setattr(
        instance_setup,
        "_resolve_rdp_password_from_secret_ref",
        lambda ref: "Real-Runtime-Password!" if ref == "arn:guest-password" else None,
    )

    def capture_run(orchestrator, execution, plan, context, document_name, failure_prefix):
        captured.update(
            orchestrator=orchestrator,
            execution=execution,
            plan=plan,
            context=context,
            document_name=document_name,
            failure_prefix=failure_prefix,
        )

    monkeypatch.setattr(instance_setup, "_run_setup_plan", capture_run)
    ctx = instance_setup._InstanceSetupCtx("workstation", "public-key", "", "Administrator")

    instance_setup._set_local_password_or_raise(
        MagicMock(),
        original_execution,
        ctx,
        {
            "private_ip": "10.20.30.40",
            "ssh_key_secret_arn": "arn:ssh-key",
            "rdp_password_secret_arn": "arn:guest-password",
        },
        platform="windows",
        failure_prefix="password push failed",
    )

    assert captured["execution"] is password_execution
    assert captured["document_name"] == "AWS-RunPowerShellScript"
    assert captured["context"] == {
        "rdp_username": "Administrator",
        "rdp_password": "Real-Runtime-Password!",
    }
    plan = captured["plan"]
    assert "Real-Runtime-Password!" not in plan.steps[0].script
    assert plan.steps[0].stdin_input == "{{ rdp_password }}\n"
    close.assert_called_once_with()


def test_aws_password_push_requires_secrets_manager_reference(monkeypatch):
    monkeypatch.setattr(instance_setup, "_get_cloud_provider", lambda: "aws")
    ctx = instance_setup._InstanceSetupCtx("workstation", "public-key", "", "Administrator")

    with pytest.raises(SetupError, match="no RDP secret reference"):
        instance_setup._set_local_password_or_raise(
            MagicMock(),
            _execution(stdout="unused"),
            ctx,
            {"rdp_password_ssm_param_name": "/obsolete/placeholder"},
            platform="windows",
            failure_prefix="password push failed",
        )
