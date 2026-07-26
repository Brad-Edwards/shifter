"""Scenario image projection for pre-bake planning (PLAT-201, #680).

Turns a scenario into the bounded image identities a single range needs, which
is what capacity scales into per-AMI pre-bake counts. CMS owns this because CMS
owns scenario hydration; the Engine consumes the projection rather than
re-parsing scenario content.

The scope split is the part that is easy to get wrong: a CTF asset declared
``shared`` exists once for the whole event, so multiplying it by concurrent
ranges would overstate pre-bake demand by the size of the cohort.
"""

from __future__ import annotations

import pytest

from cms.scenarios.images import project_scenario_images


class _Instance:
    """Stands in for a legacy ScenarioTemplate InstanceConfig."""

    def __init__(self, name, os_type, ami_key=None):
        self.name = name
        self.os_type = os_type
        self.ami_key = ami_key


class _Asset:
    """Stands in for a CTF AssetSpec."""

    def __init__(self, name, os_type, scope="per_participant", image=None):
        self.name = name
        self.os_type = os_type
        self.scope = scope
        self.image = image


class _LegacyTemplate:
    def __init__(self, instances):
        self.instances = instances


class _CtfTemplate:
    def __init__(self, assets):
        self.assets = assets


@pytest.fixture
def _load(monkeypatch):
    def _install(template):
        monkeypatch.setattr("cms.scenarios.images.load_scenario", lambda scenario_id: template)

    return _install


class TestLegacyScenarios:
    """Every legacy instance is realized once per range."""

    def test_each_instance_counts_once_per_range(self, _load):
        _load(_LegacyTemplate([_Instance("Attacker", "kali"), _Instance("Victim", "windows")]))

        projection = project_scenario_images("basic")

        counts = {(i.source_name, i.os_family): i.count for i in projection.per_range}
        assert counts == {("kali", "kali"): 1, ("windows", "windows"): 1}
        assert projection.shared == ()

    def test_repeated_os_types_accumulate(self, _load):
        _load(_LegacyTemplate([_Instance("A", "windows"), _Instance("B", "windows")]))

        projection = project_scenario_images("basic")

        assert len(projection.per_range) == 1
        assert projection.per_range[0].count == 2

    def test_ami_key_override_is_a_distinct_image_identity(self, _load):
        """A custom AMI is a different image to pre-bake than the os default."""
        _load(_LegacyTemplate([_Instance("A", "kali"), _Instance("B", "kali", ami_key="polaris-vm")]))

        names = {image.source_name for image in project_scenario_images("basic").per_range}
        assert names == {"kali", "polaris-vm"}


class TestCtfScenarios:
    """CTF assets split by scope: per-participant scales, shared does not."""

    def test_per_participant_assets_are_per_range(self, _load):
        _load(_CtfTemplate([_Asset("kali", "kali"), _Asset("dc", "windows")]))

        projection = project_scenario_images("ctf-basic")

        assert {i.source_name for i in projection.per_range} == {"kali", "windows"}
        assert projection.shared == ()

    def test_shared_assets_are_counted_once_for_the_event(self, _load):
        _load(_CtfTemplate([_Asset("kali", "kali"), _Asset("scoreboard", "ubuntu", scope="shared")]))

        projection = project_scenario_images("ctf-basic")

        assert {i.source_name for i in projection.per_range} == {"kali"}
        assert {i.source_name for i in projection.shared} == {"ubuntu"}

    def test_explicit_image_wins_over_os_type(self, _load):
        _load(_CtfTemplate([_Asset("a", "linux", image="ghcr.io/example/kali:2026.1")]))

        assert project_scenario_images("ctf-basic").per_range[0].source_name == "ghcr.io/example/kali:2026.1"


class TestUnresolvable:
    """A scenario we cannot read yields no projection -- never a fabricated one."""

    def test_missing_scenario_is_empty(self, monkeypatch):
        def _boom(scenario_id):
            raise ValueError("no such scenario")

        monkeypatch.setattr("cms.scenarios.images.load_scenario", _boom)

        projection = project_scenario_images("nope")

        assert projection.per_range == ()
        assert projection.shared == ()
        assert projection.resolved is False

    def test_resolved_flag_is_true_for_a_real_projection(self, _load):
        _load(_LegacyTemplate([_Instance("A", "kali")]))

        assert project_scenario_images("basic").resolved is True

    def test_template_without_instances_or_assets_is_empty(self, _load):
        _load(object())

        projection = project_scenario_images("weird")

        assert projection.per_range == ()
        assert projection.resolved is False


class TestSerialization:
    """The projection crosses a layer boundary inside the declaration hints."""

    def test_round_trips_through_its_wire_form(self, _load):
        _load(_CtfTemplate([_Asset("kali", "kali"), _Asset("board", "ubuntu", scope="shared")]))

        payload = project_scenario_images("ctf-basic").as_hint()

        assert payload["resolved"] is True
        assert {entry["source_name"] for entry in payload["per_range"]} == {"kali"}
        assert {entry["source_name"] for entry in payload["shared"]} == {"ubuntu"}
        assert all(
            set(entry) == {"source_name", "source_version", "os_family", "count"} for entry in payload["per_range"]
        )
