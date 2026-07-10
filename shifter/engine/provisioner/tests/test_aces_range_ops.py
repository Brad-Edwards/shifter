"""Tests for the ACES-native range lifecycle entry (ADR-031, ADR-032).

Exercises run_aces_range_provision/destroy: it reads the serialized ACES plan,
drives the GCE apply/destroy, and publishes range lifecycle status through the
neutral event seam. The GCE apply/destroy and the DB read are patched, so this
verifies the orchestration flow (status transitions, failure handling, resolver
wiring), not the cloud calls (covered by test_aces_gcp_apply).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import aces_range_ops
from aces_plan import AcesPlanImage, AcesPlanNode
from config import GCERangeImageProfile


def _serialized_plan() -> dict:
    return {
        "kind": "aces_provisioning_plan",
        "aces_sdl_version": "0.19.1",
        "resources": {
            "net.lan": {
                "address": "net.lan",
                "resource_type": "network",
                "payload": {"name": "lan", "spec": {"infrastructure": {"properties": {"cidr": "10.9.0.0/24"}}}},
            },
            "node.web": {
                "address": "node.web",
                "resource_type": "node",
                "payload": {
                    "name": "web",
                    "os_family": "linux",
                    "spec": {"node": {"source": "ubuntu"}, "infrastructure": {"networks": ["net.lan"]}},
                },
            },
        },
    }


@pytest.fixture
def patched(monkeypatch):
    calls = SimpleNamespace(
        apply=MagicMock(),
        destroy=MagicMock(),
        status=MagicMock(),
        ready=MagicMock(),
        failed=MagicMock(),
        destroyed=MagicMock(),
    )
    monkeypatch.setattr(
        aces_range_ops,
        "get_aces_range_data_by_request_id",
        lambda request_id: {"range_id": 7, "user_id": 3, "plan": _serialized_plan()},
    )
    monkeypatch.setattr(aces_range_ops, "apply_aces_range_cell", calls.apply)
    monkeypatch.setattr(aces_range_ops, "destroy_aces_range_cell", calls.destroy)
    monkeypatch.setattr(aces_range_ops, "publish_status_update", calls.status)
    monkeypatch.setattr(aces_range_ops, "publish_ready", calls.ready)
    monkeypatch.setattr(aces_range_ops, "publish_failed", calls.failed)
    monkeypatch.setattr(aces_range_ops, "publish_destroyed", calls.destroyed)
    return calls


class TestProvision:
    def test_publishes_provisioning_then_ready_and_applies(self, patched):
        aces_range_ops.run_aces_range_provision("req-1")
        patched.status.assert_called_once_with(request_id="req-1", range_id=7, user_id=3, new_status="provisioning")
        assert patched.apply.called
        assert patched.apply.call_args.args[0] == "req-1"
        assert patched.apply.call_args.args[1] == 7
        patched.ready.assert_called_once_with(request_id="req-1", range_id=7, user_id=3)
        assert not patched.failed.called

    def test_failure_publishes_failed_and_reraises(self, patched):
        patched.apply.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            aces_range_ops.run_aces_range_provision("req-1")
        assert patched.failed.called
        assert patched.failed.call_args.kwargs["error_message"].startswith("boom")
        assert not patched.ready.called


class TestDestroy:
    def test_destroys_and_publishes_destroyed(self, patched):
        aces_range_ops.run_aces_range_destroy("req-1")
        assert patched.destroy.called
        patched.destroyed.assert_called_once_with(request_id="req-1", range_id=7, user_id=3)

    def test_failure_publishes_failed_and_reraises(self, patched):
        patched.destroy.side_effect = RuntimeError("kaboom")
        with pytest.raises(RuntimeError, match="kaboom"):
            aces_range_ops.run_aces_range_destroy("req-1")
        assert patched.failed.called


def _node(image: AcesPlanImage | None) -> AcesPlanNode:
    return AcesPlanNode(
        address="node.web", name="web", os_family="linux", count=1, network_addresses=("net.lan",), image=image
    )


class TestRegistryResolver:
    def test_resolver_wires_registry_candidates_to_policy(self, monkeypatch):
        candidates = [{"source_version": None, "image_ref": "projects/x/global/images/ubuntu-1"}]
        get_candidates = MagicMock(return_value=candidates)
        resolve = MagicMock(return_value=GCERangeImageProfile(source_image="projects/x/global/images/ubuntu-1"))
        monkeypatch.setattr(aces_range_ops, "get_aces_image_candidates", get_candidates)
        monkeypatch.setattr(aces_range_ops, "resolve_gce_image", resolve)

        node = _node(AcesPlanImage(name="ubuntu"))
        profile = aces_range_ops._registry_resolver()(node)

        get_candidates.assert_called_once_with("gce", "ubuntu")
        resolve.assert_called_once_with(node, candidates)
        assert profile.source_image == "projects/x/global/images/ubuntu-1"

    def test_resolver_skips_registry_when_image_absent(self, monkeypatch):
        get_candidates = MagicMock()
        resolve = MagicMock(return_value=GCERangeImageProfile())
        monkeypatch.setattr(aces_range_ops, "get_aces_image_candidates", get_candidates)
        monkeypatch.setattr(aces_range_ops, "resolve_gce_image", resolve)

        node = _node(None)
        aces_range_ops._registry_resolver()(node)

        assert not get_candidates.called
        resolve.assert_called_once_with(node, [])
