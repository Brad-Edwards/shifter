"""Tests for the in-range-cluster guest SSH transport (RangePodSSHExecutor)."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from executors.range_pod_ssh_executor import RangePodSSHExecutor


class _ApiException(Exception):
    def __init__(self, status=500):
        self.status = status
        super().__init__(f"ApiException({status})")


def _running_pod_with_segment_ip():
    pod = MagicMock()
    pod.to_dict.return_value = {
        "status": {"phase": "Running"},
        "metadata": {
            "annotations": {
                "k8s.v1.cni.cncf.io/network-status": json.dumps([{"interface": "net1", "ips": ["10.200.2.12"]}])
            }
        },
    }
    return pod


def _make_executor(core_api):
    return RangePodSSHExecutor(
        core_api=core_api,
        client_module=MagicMock(),
        api_exception=_ApiException,
        namespace="range-7",
        network_name="range-7-core",
        runner_image="registry.example/runner:latest",
        private_key="PRIVATE-KEY-MATERIAL",
        username="kali",
    )


def test_provision_key_returns_in_pod_path_not_local_file():
    executor = _make_executor(MagicMock())
    assert executor._key_path.startswith("/tmp/shifter-guest-key-")  # noqa: S108 - in-pod path
    # No local key file is created for the range transport.
    import os

    assert not os.path.exists(executor._key_path)


def test_runner_manifest_attaches_nad_without_static_ip():
    executor = _make_executor(MagicMock())
    manifest = executor._build_runner_manifest()
    annotation = manifest["metadata"]["annotations"]["k8s.v1.cni.cncf.io/networks"]
    networks = json.loads(annotation)
    assert networks == [{"name": "range-7-core", "interface": "net1"}]
    assert manifest["spec"]["containers"][0]["image"] == "registry.example/runner:latest"


def test_ensure_runner_creates_pod_and_waits_ready(monkeypatch):
    monkeypatch.setattr("executors.range_pod_ssh_executor.time.sleep", lambda *_a, **_k: None)
    core_api = MagicMock()
    core_api.read_namespaced_pod.return_value = _running_pod_with_segment_ip()
    executor = _make_executor(core_api)

    executor._ensure_runner()

    core_api.create_namespaced_pod.assert_called_once()
    assert executor._runner_ready is True


def test_ensure_runner_tolerates_existing_pod(monkeypatch):
    monkeypatch.setattr("executors.range_pod_ssh_executor.time.sleep", lambda *_a, **_k: None)
    core_api = MagicMock()
    core_api.create_namespaced_pod.side_effect = _ApiException(status=409)
    core_api.read_namespaced_pod.return_value = _running_pod_with_segment_ip()
    executor = _make_executor(core_api)

    executor._ensure_runner()

    assert executor._runner_ready is True


def test_invoke_ssh_runs_wrapped_command_via_exec(monkeypatch):
    executor = _make_executor(MagicMock())
    executor._runner_ready = True
    executor._key_planted = True
    calls = []

    def fake_exec(command, timeout_seconds):
        calls.append((command, timeout_seconds))
        return 0, b"hello\n", b""

    monkeypatch.setattr(executor, "_exec", fake_exec)

    ssh_args = ["ssh", "-i", executor._key_path, "kali@10.200.2.10", "bash", "-se"]
    rc, out, err = executor._invoke_ssh(ssh_args, b"echo hi\n", 42)

    assert (rc, out, err) == (0, b"hello\n", b"")
    command, timeout = calls[0]
    assert command[:2] == ["/bin/sh", "-c"]
    assert timeout == 42
    wrapper = command[2]
    # The guest command script is delivered base64-encoded then piped to ssh
    # via a runner-local file (never on the guest argv).
    assert base64.b64encode(b"echo hi\n").decode() in wrapper
    assert "base64 -d" in wrapper
    assert "ssh" in wrapper and "kali@10.200.2.10" in wrapper


def test_ensure_key_planted_execs_once(monkeypatch):
    executor = _make_executor(MagicMock())
    execs = []
    monkeypatch.setattr(executor, "_exec", lambda command, timeout_seconds: execs.append(command) or (0, b"", b""))

    executor._ensure_key_planted()
    executor._ensure_key_planted()

    assert len(execs) == 1
    assert executor._key_planted is True
    assert base64.b64encode(b"PRIVATE-KEY-MATERIAL").decode() in execs[0][2]


def test_ensure_key_planted_raises_on_failure(monkeypatch):
    from executors.guest_ssh_executor import GuestSSHConnectionError

    executor = _make_executor(MagicMock())
    monkeypatch.setattr(executor, "_exec", lambda command, timeout_seconds: (1, b"", b"boom"))

    with pytest.raises(GuestSSHConnectionError, match="plant guest SSH key"):
        executor._ensure_key_planted()


@pytest.mark.parametrize(
    ("returncode_attr", "channel_payload", "expected"),
    [
        (0, None, 0),
        (None, json.dumps({"status": "Success"}), 0),
        (
            None,
            json.dumps({"status": "Failure", "details": {"causes": [{"reason": "ExitCode", "message": "255"}]}}),
            255,
        ),
        (None, json.dumps({"status": "Failure", "details": {}}), 1),
    ],
)
def test_exec_returncode_parsing(returncode_attr, channel_payload, expected):
    resp = MagicMock()
    resp.returncode = returncode_attr
    resp.read_channel.return_value = channel_payload
    assert RangePodSSHExecutor._exec_returncode(resp) == expected
