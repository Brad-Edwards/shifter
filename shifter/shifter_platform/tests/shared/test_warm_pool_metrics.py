"""Tests for ``shared.warm_pool.metrics`` (#28).

Pins the observable contract: gauge/counter shape in the WarmPool namespace,
strictly low-cardinality dimensions (no user/request/scenario ids), closed claim
outcomes, and fail-soft emission (an observability blip never raises).
"""

from __future__ import annotations

import pytest

from shared.warm_pool.metrics import (
    CLAIM_FALLBACK,
    CLAIM_HIT,
    WarmPoolBucketSnapshot,
    build_gauge_metric_data,
    emit_claim_outcome,
    emit_gauges,
)


class _FakeClient:
    def __init__(self, *, boom: bool = False):
        self.calls: list[dict] = []
        self._boom = boom

    def put_metric_data(self, **kwargs):
        if self._boom:
            raise RuntimeError("cloud blip")
        self.calls.append(kwargs)


_SNAP = WarmPoolBucketSnapshot(
    bucket_id="gce-polaris", backend="gce", region="us-central1", ready=2, provisioning=1, unhealthy=0, claimed=3
)

_ALLOWED_DIMENSIONS = {"Bucket", "Backend", "Region", "Outcome"}


class TestGauges:
    def test_metric_data_shape_and_low_cardinality(self):
        data = build_gauge_metric_data([_SNAP])
        names = {d["MetricName"] for d in data}
        assert names == {"WarmPoolReady", "WarmPoolProvisioning", "WarmPoolUnhealthy", "WarmPoolClaimed"}
        for entry in data:
            assert entry["Unit"] == "Count"
            for dim in entry["Dimensions"]:
                assert dim["Name"] in _ALLOWED_DIMENSIONS

    def test_emit_gauges_uses_warm_pool_namespace(self):
        client = _FakeClient()
        assert emit_gauges([_SNAP], client=client) is True
        assert client.calls[0]["Namespace"] == "Shifter/WarmPool"

    def test_empty_snapshots_is_noop(self):
        client = _FakeClient()
        assert emit_gauges([], client=client) is True
        assert client.calls == []

    def test_emit_is_fail_soft(self):
        assert emit_gauges([_SNAP], client=_FakeClient(boom=True)) is False


class TestClaimOutcome:
    @pytest.mark.parametrize("outcome", [CLAIM_HIT, CLAIM_FALLBACK])
    def test_closed_outcomes_emit(self, outcome):
        client = _FakeClient()
        assert emit_claim_outcome(bucket_id="b", backend="gce", outcome=outcome, client=client) is True
        entry = client.calls[0]["MetricData"][0]
        assert entry["MetricName"] == "WarmPoolClaimOutcome"
        assert {d["Name"] for d in entry["Dimensions"]} <= _ALLOWED_DIMENSIONS

    def test_unknown_outcome_dropped(self):
        client = _FakeClient()
        assert emit_claim_outcome(bucket_id="b", backend="gce", outcome="weird", client=client) is False
        assert client.calls == []

    def test_fail_soft(self):
        assert (
            emit_claim_outcome(bucket_id="b", backend="gce", outcome=CLAIM_HIT, client=_FakeClient(boom=True)) is False
        )
