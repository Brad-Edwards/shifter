"""Warm-pool reconciler tests (#28).

The load-bearing correctness property: the digest the reconciler stamps on a
prepared generation must equal the digest the launch claim path computes for the
warm target class, or a prepared generation is never claimable. Also pins the
disabled-policy no-op.
"""

from __future__ import annotations

from cms.services._warm_pool_claim import ISOLATION_PERSONAL, build_compatibility_key
from cms.services._warm_pool_reconcile import (
    WARM_PURPOSE,
    WARM_RANGE_SOURCE,
    _bucket_compatibility_digest,
    reconcile_warm_pool,
)
from shared.warm_pool.compatibility import compatibility_digest
from shared.warm_pool.policy import load_policy_json

_ENABLED = load_policy_json(
    '{"enabled": true, "buckets": [{"id": "gce-polaris", "backend": "gce", "scenario": "polaris",'
    ' "capacity_partition": "default", "target": 1, "minimum": 0, "maximum": 2, "idle_ttl_seconds": 3600,'
    ' "region": "us-central1", "access_mode": "vpn"}]}'
)
_DISABLED = load_policy_json("")


def test_disabled_policy_is_noop(monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "WARM_POOL_POLICY", _DISABLED, raising=False)
    summary = reconcile_warm_pool()
    assert summary == {"provisioned": 0, "retired": 0, "finalized": 0, "buckets": 0}


def test_reconciler_digest_matches_launch_claim_digest():
    """A generation the reconciler prepares is claimable by the warm target launch."""
    bucket = _ENABLED.buckets[0]
    package_digest = "sha256:" + "1" * 64
    lock_digest = "sha256:" + "2" * 64

    reconciler_digest = _bucket_compatibility_digest(bucket, package_digest=package_digest, lock_digest=lock_digest)

    # The launch claim path's key for a personal-workspace Mission Control live-fire
    # launch of the same scenario on the same backend, with the deployment-default
    # egress, must produce the same digest.
    launch_digest = compatibility_digest(
        build_compatibility_key(
            backend="gce",
            instantiation_purpose=WARM_PURPOSE.value,
            range_source=WARM_RANGE_SOURCE.value,
            workspace_isolation_class=ISOLATION_PERSONAL,
            egress_mode="status-quo",
            scenario=bucket.scenario,
            package_digest=package_digest,
            lock_digest=lock_digest,
        )
    )
    assert reconciler_digest == launch_digest


def test_reconciler_digest_changes_with_package(monkeypatch):
    bucket = _ENABLED.buckets[0]
    a = _bucket_compatibility_digest(bucket, package_digest="sha256:a", lock_digest="sha256:x")
    b = _bucket_compatibility_digest(bucket, package_digest="sha256:b", lock_digest="sha256:x")
    assert a != b
