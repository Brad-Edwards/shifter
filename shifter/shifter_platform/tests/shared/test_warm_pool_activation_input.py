"""Tests for ``shared.warm_pool.activation_input`` (#28).

The activation input is the only warm operation carrying a claimant identity; it
must be a closed, bounded, reference-only projection. These tests pin:

- round-trip build -> parse;
- fail-closed on unknown/missing keys, wrong schema tag, non-positive identity,
  over-long username, blank fence;
- no secret/inventory/policy field is accepted (extra keys rejected).
"""

from __future__ import annotations

import pytest

from shared.raes.operation_input import RaesInputBindings, build_raes_operation_input
from shared.warm_pool.activation_input import (
    ACTIVATION_SCHEMA,
    ActivationInputError,
    build_activation_input,
    parse_activation_input,
)


def _raes_input():
    return build_raes_operation_input(
        plan={},
        bindings=RaesInputBindings(delivery=()),
        image_candidates={},
        range_backend="gce",
        instantiation_purpose="live_fire",
        legacy_range_id=1001,
    )


def _payload(**overrides):
    base = {
        "claimant_user_id": 42,
        "claimant_username": "claimant@example.com",
        "workspace_id": 7,
        "range_source": "mission-control",
        "instantiation_purpose": "live_fire",
        "range_backend": "gce",
        "legacy_range_id": 1001,
        "compatibility_digest": "sha256:" + "a" * 64,
        "prepared_generation_fence": "7f000000-0000-4000-8000-000000000001",
        "raes_input": _raes_input(),
    }
    base.update(overrides)
    return base


class TestRoundTrip:
    def test_build_then_parse(self):
        payload = build_activation_input(**_payload())
        parsed = parse_activation_input(payload)
        assert parsed.claimant_user_id == 42
        assert parsed.claimant_username == "claimant@example.com"
        assert parsed.range_backend == "gce"
        assert payload["schema"] == ACTIVATION_SCHEMA


class TestFailClosed:
    def test_unknown_key_rejected(self):
        payload = _payload()
        payload["schema"] = ACTIVATION_SCHEMA
        payload["secret_token"] = "nope"
        with pytest.raises(ActivationInputError) as exc:
            parse_activation_input(payload)
        assert "unexpected" in str(exc.value)

    def test_missing_key_rejected(self):
        payload = build_activation_input(**_payload())
        del payload["claimant_user_id"]
        with pytest.raises(ActivationInputError) as exc:
            parse_activation_input(payload)
        assert "missing" in str(exc.value)

    def test_wrong_schema_rejected(self):
        payload = build_activation_input(**_payload())
        payload["schema"] = "range-warm-activation/v2"
        with pytest.raises(ActivationInputError):
            parse_activation_input(payload)

    @pytest.mark.parametrize("bad", [0, -1, True, "5"])
    def test_non_positive_identity_rejected(self, bad):
        with pytest.raises(ActivationInputError):
            build_activation_input(**_payload(claimant_user_id=bad))

    def test_over_long_username_rejected(self):
        with pytest.raises(ActivationInputError):
            build_activation_input(**_payload(claimant_username="x" * 300))

    def test_blank_fence_rejected(self):
        with pytest.raises(ActivationInputError):
            build_activation_input(**_payload(prepared_generation_fence="  "))

    def test_non_mapping_rejected(self):
        with pytest.raises(ActivationInputError):
            parse_activation_input(["not", "a", "mapping"])
