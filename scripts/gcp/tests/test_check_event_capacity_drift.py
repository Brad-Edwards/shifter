"""Read-only event-capacity drift comparison tests (#1816)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check_event_capacity_drift.py"
_SPEC = importlib.util.spec_from_file_location("check_event_capacity_drift", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

DriftInputError = _MODULE.DriftInputError
collect_live_state = _MODULE.collect_live_state
compare_capacity_state = _MODULE.compare_capacity_state
render_drift_report = _MODULE.render_drift_report


def _desired() -> dict:
    return {
        "profile_id": "gcp-shared-v1-p30",
        "terraform": {
            "cloud_sql.tier": "db-custom-4-15360",
            "redis.memory_size_gb": 8,
        },
        "kubernetes": {
            "deployment/portal-web.spec.replicas": 5,
            "deployment/guacd.spec.replicas": 2,
        },
    }


def test_exact_live_state_has_no_drift():
    desired = _desired()
    observed = {
        "profile_id": desired["profile_id"],
        "terraform": dict(desired["terraform"]),
        "kubernetes": dict(desired["kubernetes"]),
    }

    assert compare_capacity_state(desired, observed) == []


def test_drift_report_names_field_without_dumping_provider_payload():
    desired = _desired()
    observed = {
        "profile_id": desired["profile_id"],
        "terraform": {**desired["terraform"], "redis.memory_size_gb": 1},
        "kubernetes": dict(desired["kubernetes"]),
        "raw_provider_response": {"password": "must-not-appear"},
    }

    report = render_drift_report(compare_capacity_state(desired, observed))

    assert "redis.memory_size_gb" in report
    assert "desired=8" in report
    assert "observed=1" in report
    assert "must-not-appear" not in report


def test_missing_observation_fails_closed():
    with pytest.raises(DriftInputError, match="missing observed field"):
        compare_capacity_state(_desired(), {"profile_id": "gcp-shared-v1-p30", "terraform": {}, "kubernetes": {}})


def test_cloud_sql_disk_is_a_non_shrinking_floor_during_profile_scale_down():
    desired = {
        "profile_id": "gcp-shared-v1-p10",
        "terraform": {"capacity.cloud_sql_disk_size_gb": 20},
        "kubernetes": {},
    }

    retained_larger_disk = {
        "profile_id": desired["profile_id"],
        "terraform": {"capacity.cloud_sql_disk_size_gb": 60},
        "kubernetes": {},
    }
    undersized_disk = {
        "profile_id": desired["profile_id"],
        "terraform": {"capacity.cloud_sql_disk_size_gb": 10},
        "kubernetes": {},
    }

    assert compare_capacity_state(desired, retained_larger_disk) == []
    assert [drift.field for drift in compare_capacity_state(desired, undersized_disk)] == [
        "capacity.cloud_sql_disk_size_gb"
    ]


def _live_responses(*, access_label: str | None = "gcp-shared-v1-p30") -> list[dict]:
    access_labels = {} if access_label is None else {"shifter_capacity_profile": access_label}
    return [
        {
            "settings": {
                "userLabels": {"shifter_capacity_profile": "gcp-shared-v1-p30"},
                "tier": "db-custom-4-15360",
                "availabilityType": "REGIONAL",
                "dataDiskSizeGb": "100",
            }
        },
        {
            "labels": {"shifter_capacity_profile": "gcp-shared-v1-p30"},
            "tier": "STANDARD_HA",
            "memorySizeGb": 8,
        },
        {
            "config": {"labels": access_labels, "machineType": "e2-standard-8"},
            "initialNodeCount": 99,
            "autoscaling": {"minNodeCount": 3, "maxNodeCount": 8},
        },
        {"items": []},
        {"items": []},
        {"items": []},
        {"data": {}},
    ]


def test_live_state_requires_capacity_profile_label_on_every_provider_resource(monkeypatch):
    responses = iter(_live_responses(access_label=None))
    monkeypatch.setattr(_MODULE, "_run_json", lambda _argv: next(responses))

    with pytest.raises(DriftInputError, match="missing or inconsistent"):
        collect_live_state(
            {},
            project="project",
            region="region",
            sql_instance="sql",
            redis_instance="redis",
            cluster="cluster",
            namespace="namespace",
        )


def test_live_state_uses_effective_autoscaling_minimum_for_access_capacity(monkeypatch):
    responses = iter(_live_responses())
    monkeypatch.setattr(_MODULE, "_run_json", lambda _argv: next(responses))

    observed = collect_live_state(
        {},
        project="project",
        region="region",
        sql_instance="sql",
        redis_instance="redis",
        cluster="cluster",
        namespace="namespace",
    )

    assert observed["terraform"]["capacity.access_node_count"] == 3
