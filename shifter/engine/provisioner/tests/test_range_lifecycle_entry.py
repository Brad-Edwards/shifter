"""Unit tests for _build_range_lifecycle_entry dispatch (issue #779 burndown)."""

import pytest

from range_ops import _build_range_lifecycle_entry


def test_aws_entry_with_instance_id():
    entry = _build_range_lifecycle_entry(
        "req-1", "uuid-1", {"cloud_provider": "aws", "aws_instance_id": "i-123"}, "victim", "vm1"
    )
    assert entry is not None
    assert entry["operation_mode"] == "aws"
    assert entry["aws_instance_id"] == "i-123"
    assert entry["uuid"] == "uuid-1"


def test_aws_entry_missing_instance_id_is_skipped():
    entry = _build_range_lifecycle_entry("req-1", "uuid-1", {"cloud_provider": "aws"}, "victim", None)
    assert entry is None


def test_gcp_vm_runtime_entry():
    entry = _build_range_lifecycle_entry(
        "req-1", "uuid-2", {"cloud_provider": "gcp", "asset_type": "vm_runtime_vm"}, "attacker", "vm2"
    )
    assert entry is not None
    assert entry["operation_mode"] == "gdc_vm_runtime"


def test_gcp_scenario_pod_entry():
    entry = _build_range_lifecycle_entry(
        "req-1", "uuid-3", {"cloud_provider": "gcp", "asset_type": "scenario_pod"}, "victim", "pod1"
    )
    assert entry is not None
    assert entry["operation_mode"] == "gdc_scenario_pod"


def test_unsupported_target_raises():
    with pytest.raises(ValueError, match="Unsupported range lifecycle target"):
        _build_range_lifecycle_entry("req-9", "uuid-9", {"cloud_provider": "azure", "asset_type": "vm"}, "victim", "x")


def test_non_dict_state_defaults_to_aws():
    # A non-dict state falls back to the aws default provider and is skipped
    # without an aws_instance_id.
    assert _build_range_lifecycle_entry("req-1", "uuid-1", None, "victim", None) is None
