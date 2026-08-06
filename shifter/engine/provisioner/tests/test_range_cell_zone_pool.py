"""Multi-region range placement.

Compute CPU quota is enforced per project *per region*, so a range fleet larger
than one region's quota must span regions. ``RANGE_NETWORK_ZONES`` supplies the
pool and each range's own allocation slot picks its zone, so create and destroy
resolve the same placement without persisting extra state.
"""

from __future__ import annotations

import pytest

from config._gce import range_cell_config_for_slot, range_cell_zone_pool


@pytest.fixture
def base_config():
    from config import GCERangeCellConfig

    return GCERangeCellConfig(
        project_id="proj",
        region="us-central1",
        zone="us-central1-a",
        network_mode="shared-vpc",
    )


def test_pool_is_empty_when_unset(monkeypatch):
    monkeypatch.delenv("RANGE_NETWORK_ZONES", raising=False)
    assert range_cell_zone_pool() == ()


def test_pool_parses_and_trims(monkeypatch):
    monkeypatch.setenv("RANGE_NETWORK_ZONES", " us-central1-a , us-east4-a ,, us-east1-b ")
    assert range_cell_zone_pool() == ("us-central1-a", "us-east4-a", "us-east1-b")


def test_unset_pool_leaves_the_config_untouched(monkeypatch, base_config):
    """Single-region deployments keep their configured zone."""
    monkeypatch.delenv("RANGE_NETWORK_ZONES", raising=False)

    assert range_cell_config_for_slot(base_config, 7) is base_config


def test_missing_slot_leaves_the_config_untouched(monkeypatch, base_config):
    """A caller without an allocation slot must not be silently re-homed."""
    monkeypatch.setenv("RANGE_NETWORK_ZONES", "us-central1-a,us-east4-a")

    assert range_cell_config_for_slot(base_config, None) is base_config


def test_slot_selects_the_zone_and_derives_its_region(monkeypatch, base_config):
    monkeypatch.setenv("RANGE_NETWORK_ZONES", "us-central1-a,us-east4-a,us-east1-b")

    assert [
        (range_cell_config_for_slot(base_config, s).zone, range_cell_config_for_slot(base_config, s).region)
        for s in range(4)
    ] == [
        ("us-central1-a", "us-central1"),
        ("us-east4-a", "us-east4"),
        ("us-east1-b", "us-east1"),
        ("us-central1-a", "us-central1"),
    ]


def test_placement_is_stable_for_a_given_slot(monkeypatch, base_config):
    """Destroy re-derives placement from the same slot, so it must not drift."""
    monkeypatch.setenv("RANGE_NETWORK_ZONES", "us-central1-a,us-east4-a,us-east1-b")

    first = range_cell_config_for_slot(base_config, 41)
    second = range_cell_config_for_slot(base_config, 41)

    assert (first.zone, first.region) == (second.zone, second.region)


def test_pool_spreads_a_fleet_evenly_across_regions(monkeypatch, base_config):
    """300 ranges over 3 zones is 100 each -- the property the quota split relies on."""
    monkeypatch.setenv("RANGE_NETWORK_ZONES", "us-central1-a,us-east4-a,us-east1-b")

    from collections import Counter

    spread = Counter(range_cell_config_for_slot(base_config, s).region for s in range(300))

    assert spread == {"us-central1": 100, "us-east4": 100, "us-east1": 100}
