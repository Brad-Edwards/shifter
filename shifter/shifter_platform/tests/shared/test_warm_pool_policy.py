"""Tests for ``shared.warm_pool.policy`` (#28): runtime parser + narrowing resolver.

Pins: disabled-by-default parse; fail-closed on malformed projection; and the
core security property of the override resolver -- an override may only *narrow*
deployment-owned limits, and any widening (raise a max, add/unknown bucket, raise
concurrency/cost) is rejected, never clamped.
"""

from __future__ import annotations

import json

import pytest

from shared.warm_pool.policy import (
    WarmPoolOverride,
    WarmPoolPolicyError,
    load_policy_json,
    resolve_effective_policy,
)


def _projection(**overrides):
    base = {
        "enabled": True,
        "replenish_interval_seconds": 300,
        "replenish_concurrency": 4,
        "max_total_ready": 10,
        "scale_down": "oldest-first",
        "replacement": "destroy-and-replace",
        "buckets": [
            {
                "id": "gce-polaris",
                "backend": "gce",
                "scenario": "polaris",
                "capacity_partition": "default",
                "target": 3,
                "minimum": 1,
                "maximum": 5,
                "idle_ttl_seconds": 3600,
            }
        ],
    }
    base.update(overrides)
    return json.dumps(base)


class TestParse:
    def test_empty_is_disabled(self):
        policy = load_policy_json("")
        assert policy.enabled is False
        assert policy.is_active() is False

    def test_valid_projection(self):
        policy = load_policy_json(_projection())
        assert policy.is_active() is True
        assert policy.bucket("gce-polaris").maximum == 5

    def test_invalid_json_rejected(self):
        with pytest.raises(WarmPoolPolicyError):
            load_policy_json("{not json")

    def test_bad_ordering_rejected(self):
        with pytest.raises(WarmPoolPolicyError):
            load_policy_json(
                _projection(
                    buckets=[
                        {
                            "id": "b",
                            "backend": "gce",
                            "scenario": "s",
                            "capacity_partition": "default",
                            "target": 9,
                            "minimum": 1,
                            "maximum": 5,
                            "idle_ttl_seconds": 60,
                        }
                    ]
                )
            )

    def test_duplicate_bucket_ids_rejected(self):
        dup = [
            {
                "id": "b",
                "backend": "gce",
                "scenario": "s",
                "capacity_partition": "default",
                "target": 1,
                "minimum": 0,
                "maximum": 2,
                "idle_ttl_seconds": 60,
            }
        ] * 2
        with pytest.raises(WarmPoolPolicyError):
            load_policy_json(_projection(buckets=dup))

    def test_unknown_scale_down_rejected(self):
        with pytest.raises(WarmPoolPolicyError):
            load_policy_json(_projection(scale_down="random"))


class TestResolveNarrowing:
    def test_no_override_returns_deployment(self):
        dep = load_policy_json(_projection())
        assert resolve_effective_policy(dep, None) is dep
        assert resolve_effective_policy(dep, WarmPoolOverride()) is dep

    def test_disable_forces_off(self):
        dep = load_policy_json(_projection())
        eff = resolve_effective_policy(dep, WarmPoolOverride(disable=True))
        assert eff.enabled is False
        assert eff.buckets == ()

    def test_restrict_buckets_subset(self):
        dep = load_policy_json(_projection())
        eff = resolve_effective_policy(dep, WarmPoolOverride(bucket_ids=()))
        assert eff.buckets == ()

    def test_unknown_bucket_rejected(self):
        dep = load_policy_json(_projection())
        with pytest.raises(WarmPoolPolicyError):
            resolve_effective_policy(dep, WarmPoolOverride(bucket_ids=("nope",)))

    def test_narrow_concurrency_down_ok(self):
        dep = load_policy_json(_projection())
        eff = resolve_effective_policy(dep, WarmPoolOverride(replenish_concurrency=1))
        assert eff.replenish_concurrency == 1

    def test_widen_concurrency_rejected(self):
        dep = load_policy_json(_projection())
        with pytest.raises(WarmPoolPolicyError) as exc:
            resolve_effective_policy(dep, WarmPoolOverride(replenish_concurrency=99))
        assert "may not exceed" in str(exc.value)

    def test_narrow_bucket_cap_ok(self):
        dep = load_policy_json(_projection())
        eff = resolve_effective_policy(dep, WarmPoolOverride(bucket_caps={"gce-polaris": {"maximum": 2, "target": 2}}))
        assert eff.bucket("gce-polaris").maximum == 2

    def test_widen_bucket_cap_rejected(self):
        dep = load_policy_json(_projection())
        with pytest.raises(WarmPoolPolicyError):
            resolve_effective_policy(dep, WarmPoolOverride(bucket_caps={"gce-polaris": {"maximum": 99}}))

    def test_override_cannot_set_unknown_bucket_field(self):
        dep = load_policy_json(_projection())
        with pytest.raises(WarmPoolPolicyError):
            resolve_effective_policy(dep, WarmPoolOverride(bucket_caps={"gce-polaris": {"backend": 1}}))

    def test_widen_cost_ceiling_rejected(self):
        dep = load_policy_json(_projection(cost_ceiling=100.0))
        with pytest.raises(WarmPoolPolicyError):
            resolve_effective_policy(dep, WarmPoolOverride(cost_ceiling=999.0))
