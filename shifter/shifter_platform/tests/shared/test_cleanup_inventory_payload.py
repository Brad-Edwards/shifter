"""Terminal destroy result carries bounded provider inventory/readback evidence (#2086, ADR-063-R4)."""

from __future__ import annotations

import pytest

from shared.operation_results import OperationResultError, ResultStep, parse_result_payload

_STEP = ResultStep.RAES_TERMINAL_DESTROYED


def test_destroy_result_accepts_verified_absent_inventory():
    payload = {
        "raes_status": "succeeded",
        "cleanup_inventory": {
            "outcome": "VERIFIED_ABSENT",
            "residual_categories": [],
            "scope": {"project": "proj-x", "categories": ["instances"]},
        },
    }
    parsed = parse_result_payload("raes-range", "destroy", step=_STEP, payload=payload)
    assert parsed["cleanup_inventory"]["outcome"] == "VERIFIED_ABSENT"


def test_destroy_result_accepts_residuals():
    payload = {
        "raes_status": "succeeded",
        "cleanup_inventory": {
            "outcome": "RESIDUALS_FOUND",
            "residual_categories": [{"category": "instances", "count": 1}],
            "scope": {"project": "proj-x"},
        },
    }
    parsed = parse_result_payload("raes-range", "destroy", step=_STEP, payload=payload)
    assert parsed["cleanup_inventory"]["residual_categories"][0]["category"] == "instances"


def test_destroy_result_rejects_unknown_outcome():
    payload = {
        "raes_status": "succeeded",
        "cleanup_inventory": {"outcome": "DONE", "residual_categories": [], "scope": {}},
    }
    with pytest.raises(OperationResultError, match="outcome"):
        parse_result_payload("raes-range", "destroy", step=_STEP, payload=payload)


def test_destroy_result_rejects_unexpected_inventory_field():
    payload = {
        "raes_status": "succeeded",
        "cleanup_inventory": {"outcome": "VERIFIED_ABSENT", "residual_categories": [], "scope": {}, "extra": 1},
    }
    with pytest.raises(OperationResultError, match="unexpected field"):
        parse_result_payload("raes-range", "destroy", step=_STEP, payload=payload)


def test_destroy_result_without_inventory_still_valid():
    parsed = parse_result_payload("raes-range", "destroy", step=_STEP, payload={"raes_status": "succeeded"})
    assert "cleanup_inventory" not in parsed
