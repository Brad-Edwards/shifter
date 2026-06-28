"""Tests for provider-routed guest executor selection."""

from __future__ import annotations

import pytest

from executors.factory import (
    build_guest_execution_context,
    get_setup_document_name,
    get_ssh_username,
)


class TestGuestExecutorFactoryHelpers:
    """Pure helper tests."""

    def test_get_setup_document_name_maps_windows_to_powershell(self):
        assert get_setup_document_name("windows") == "AWS-RunPowerShellScript"

    def test_get_setup_document_name_maps_linux_to_shell(self):
        assert get_setup_document_name("ubuntu") == "AWS-RunShellScript"

    def test_get_ssh_username_maps_known_os_types(self):
        assert get_ssh_username("kali", "attacker") == "kali"
        assert get_ssh_username("amazon-linux", "victim") == "ec2-user"
        assert get_ssh_username("windows", "victim") == "Administrator"
        assert get_ssh_username("ubuntu", "dc") == "Administrator"


class TestBuildGuestExecutionContext:
    """Provider-aware setup transport selection."""

    def test_aws_uses_ssm_instance_target(self, mocker, monkeypatch):
        monkeypatch.delenv("CLOUD_PROVIDER", raising=False)
        mock_executor_cls = mocker.patch("executors.factory.SSMExecutor")
        mock_executor = mock_executor_cls.return_value

        context = build_guest_execution_context({"instance_id": "i-1234567890", "os": "ubuntu", "role": "victim"})

        assert context.executor is mock_executor
        assert context.target == "i-1234567890"
        assert context.document_name == "AWS-RunShellScript"
        assert context.transport_name == "ssm"
        mock_executor_cls.assert_called_once_with()
        context.close()
        mock_executor.close.assert_called_once_with()

    def _gcp_instance(self, **overrides):
        data = {
            "instance_id": "range-vm-1",
            "private_ip": "10.200.2.10",
            "ssh_key_secret_arn": "projects/test/secrets/range-vm-1-key",
            "gdc_namespace": "range-7",
            "gdc_nad_name": "range-7-core",
            "os": "windows",
            "role": "victim",
        }
        data.update(overrides)
        return data

    def _patch_gcp_deps(self, mocker, monkeypatch, key_material="PRIVATE KEY"):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        monkeypatch.setenv("GDC_SETUP_RUNNER_IMAGE", "registry.example/runner:latest")
        mock_store = mocker.Mock()
        mock_store.get_secret.return_value = key_material
        mocker.patch("executors.factory.get_secrets_store", return_value=mock_store)
        mocker.patch(
            "executors.factory._build_range_kube_clients",
            return_value=("CORE_API", "CLIENT_MODULE", RuntimeError),
        )
        mock_executor_cls = mocker.patch(
            "executors.range_pod_ssh_executor.RangePodSSHExecutor",
        )
        return mock_store, mock_executor_cls

    def test_gcp_uses_in_range_pod_transport(self, mocker, monkeypatch):
        mock_store, mock_executor_cls = self._patch_gcp_deps(mocker, monkeypatch)
        mock_executor = mock_executor_cls.return_value

        context = build_guest_execution_context(self._gcp_instance())

        assert context.executor is mock_executor
        assert context.target == "10.200.2.10"
        assert context.document_name == "AWS-RunPowerShellScript"
        assert context.transport_name == "range-pod-ssh"
        mock_store.get_secret.assert_called_once_with("projects/test/secrets/range-vm-1-key")
        mock_executor_cls.assert_called_once_with(
            core_api="CORE_API",
            client_module="CLIENT_MODULE",
            api_exception=RuntimeError,
            namespace="range-7",
            network_name="range-7-core",
            runner_image="registry.example/runner:latest",
            private_key="PRIVATE KEY",
            username="Administrator",
        )

        context.close()
        mock_executor.close.assert_called_once_with()

    def test_gcp_prefers_explicit_ssh_username_from_instance_output(self, mocker, monkeypatch):
        _store, mock_executor_cls = self._patch_gcp_deps(mocker, monkeypatch)

        build_guest_execution_context(self._gcp_instance(ssh_username="custom-user", os="ubuntu"))

        assert mock_executor_cls.call_args.kwargs["username"] == "custom-user"

    def test_gcp_runner_image_falls_back_to_engine_task_image(self, mocker, monkeypatch):
        _store, mock_executor_cls = self._patch_gcp_deps(mocker, monkeypatch)
        monkeypatch.delenv("GDC_SETUP_RUNNER_IMAGE", raising=False)
        monkeypatch.setenv("ENGINE_TASK_IMAGE", "registry.example/provisioner:sha")

        build_guest_execution_context(self._gcp_instance())

        assert mock_executor_cls.call_args.kwargs["runner_image"] == "registry.example/provisioner:sha"

    def test_gcp_requires_private_ip(self, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")

        try:
            build_guest_execution_context(
                {
                    "instance_id": "range-vm-1",
                    "ssh_key_secret_arn": "projects/test/secrets/range-vm-1-key",
                    "gdc_namespace": "range-7",
                    "gdc_nad_name": "range-7-core",
                    "os": "ubuntu",
                    "role": "victim",
                }
            )
        except ValueError as exc:
            assert "private_ip" in str(exc)
        else:
            raise AssertionError("Expected ValueError for missing private_ip")

    def test_gcp_requires_gdc_network_fields(self, mocker, monkeypatch):
        monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
        monkeypatch.setenv("GDC_SETUP_RUNNER_IMAGE", "registry.example/runner:latest")
        mock_store = mocker.Mock()
        mock_store.get_secret.return_value = "PRIVATE KEY"
        mocker.patch("executors.factory.get_secrets_store", return_value=mock_store)

        with pytest.raises(ValueError, match="gdc_namespace"):
            build_guest_execution_context(
                {
                    "private_ip": "10.200.2.10",
                    "ssh_key_secret_arn": "projects/test/secrets/range-vm-1-key",
                    "os": "ubuntu",
                    "role": "victim",
                }
            )
