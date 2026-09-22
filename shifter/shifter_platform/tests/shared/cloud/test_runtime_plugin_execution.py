"""Runtime-plugin result acceptance is independent of terminal housekeeping."""

from uuid import uuid4

from shifter_adapter_sdk.runtime import PROTOCOL, InspectionInput, InspectionResult, PluginManifest

from shared.cloud.exceptions import CloudTaskError
from shared.cloud.runtime_plugins import observe_plugin


def _request() -> InspectionInput:
    manifest = PluginManifest.model_validate(
        {
            "protocol": PROTOCOL,
            "plugin_id": "example.adapter",
            "version": "1.0",
            "distribution": "example-adapter",
            "entry_point": "example",
            "worker_image": "registry.example.test/adapters/example@sha256:" + "a" * 64,
            "capabilities": ["guest.verify"],
            "required_bindings": ["server"],
        }
    )
    return InspectionInput(protocol=PROTOCOL, phase="inspect", invocation_id=uuid4(), manifest=manifest)


def test_valid_result_survives_deferred_terminal_cleanup(monkeypatch, caplog):
    request = _request()
    expected = InspectionResult(
        protocol=request.protocol,
        phase=request.phase,
        invocation_id=request.invocation_id,
        input_digest=request.digest,
        status="compatible",
    )

    monkeypatch.setattr(
        "shared.cloud.runtime_plugins.KubernetesTaskRunner.get_task_output",
        lambda *args, **kwargs: expected.model_dump_json().encode(),
    )

    def fail_cleanup(*args, **kwargs):
        raise CloudTaskError("provider detail must remain private")

    monkeypatch.setattr("shared.cloud.runtime_plugins.KubernetesTaskRunner.delete_completed_task", fail_cleanup)

    assert observe_plugin(request) == expected
    assert "terminal cleanup deferred" in caplog.text
    assert "provider detail" not in caplog.text
