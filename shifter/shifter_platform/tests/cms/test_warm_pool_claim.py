"""Tests for the CMS warm-pool claim decision gates (#28).

The atomic claim + one-winner concurrency is proven at the Engine layer
(``tests/engine/services/test_warm_pool_claim.py``). These tests pin the CMS
claim path's *decision* gates -- the conditions under which a launch cold-falls-back
without ever touching the ledger: a disabled policy, an unsupported backend, or no
bucket serving the requested backend+scenario. Each must return ``None`` (cold
fallback) and must not raise.
"""

from __future__ import annotations

from uuid import uuid4

from cms.services._warm_pool_claim import attempt_warm_claim, build_compatibility_key
from shared.enums import RangeSource
from shared.range_instantiation_policy import InstantiationPurpose
from shared.warm_pool.compatibility import compatibility_digest
from shared.warm_pool.policy import load_policy_json

_DISABLED = load_policy_json("")
_ENABLED_GCE = load_policy_json(
    '{"enabled": true, "buckets": [{"id": "gce-polaris", "backend": "gce", "scenario": "polaris",'
    ' "capacity_partition": "default", "target": 1, "minimum": 0, "maximum": 2, "idle_ttl_seconds": 3600}]}'
)


def _attempt(backend: str, scenario: str):
    return attempt_warm_claim(
        user=None,  # unused on the gate-only paths (no bucket match / disabled / unsupported)
        scenario=scenario,
        package_digest="sha256:aaa",
        lock_digest="sha256:bbb",
        backend=backend,
        instantiation_purpose=InstantiationPurpose.LIVE_FIRE,
        range_source=RangeSource.MISSION_CONTROL,
        workspace_id=1,
        egress_mode="status-quo",
        request_id=uuid4(),
    )


class TestDecisionGates:
    def test_disabled_policy_cold_falls_back(self, monkeypatch):
        from django.conf import settings

        monkeypatch.setattr(settings, "WARM_POOL_POLICY", _DISABLED, raising=False)
        assert _attempt("gce", "polaris") is None

    def test_unsupported_backend_cold_falls_back(self, monkeypatch):
        from django.conf import settings

        # A bucket may target gdc, but gdc has no warm-activation adapter.
        policy = load_policy_json(
            '{"enabled": true, "buckets": [{"id": "gdc-x", "backend": "gdc", "scenario": "polaris",'
            ' "capacity_partition": "default", "target": 1, "minimum": 0, "maximum": 2, "idle_ttl_seconds": 3600}]}'
        )
        monkeypatch.setattr(settings, "WARM_POOL_POLICY", policy, raising=False)
        assert _attempt("gdc", "polaris") is None

    def test_no_matching_bucket_cold_falls_back(self, monkeypatch):
        from django.conf import settings

        monkeypatch.setattr(settings, "WARM_POOL_POLICY", _ENABLED_GCE, raising=False)
        # Enabled + gce supported, but no bucket serves this scenario.
        assert _attempt("gce", "some-other-scenario") is None


class TestCompatibilityKeyBuilder:
    def test_builder_is_deterministic(self):
        kwargs = {
            "backend": "gce",
            "instantiation_purpose": "live_fire",
            "range_source": "mission-control",
            "workspace_isolation_class": "personal",
            "egress_mode": "status-quo",
            "scenario": "polaris",
            "package_digest": "sha256:aaa",
            "lock_digest": "sha256:bbb",
        }
        assert compatibility_digest(build_compatibility_key(**kwargs)) == compatibility_digest(
            build_compatibility_key(**kwargs)
        )
