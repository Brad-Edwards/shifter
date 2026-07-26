"""No-dispatch backend realizability assessment for ACES scenarios (#1581, ADR-034-R3).

ADR-034-R3 requires ingestion to validate realizability against the backend
manifest and surface non-realizability to the author "without creating
loopholes". These tests pin the capability half of that assessment: the real
ACES compile/plan/validate path is executed, ``apply`` never is, and every
out-of-envelope term becomes a bounded typed gap rather than an exception or a
raw diagnostic string.

The critical guarantee is negative: an authoring-time check must never realize
anything. The dispatch port used here raises on any call, so a regression that
reintroduces ``apply`` fails these tests rather than provisioning a range.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.aces.realizability import (
    GapCategory,
    ImageDemand,
    RealizabilityOutcome,
    assess_scenario_capability,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "launchable"
_MINIMAL = _FIXTURES / "shifter-launch-min.sdl.yaml"


def _variant(tmp_path: Path, *, replace: tuple[str, str]) -> Path:
    """Write a copy of the minimal fixture with one authored term swapped."""
    source = _MINIMAL.read_text(encoding="utf-8")
    old, new = replace
    assert old in source, f"fixture no longer contains {old!r}"
    path = tmp_path / "variant.sdl.yaml"
    path.write_text(source.replace(old, new), encoding="utf-8")
    return path


class TestRealizableScenario:
    """A scenario inside the declared envelope is realizable with no gaps."""

    def test_minimal_scenario_is_realizable(self) -> None:
        assessment = assess_scenario_capability(_MINIMAL)
        assert assessment.outcome is RealizabilityOutcome.REALIZABLE
        assert assessment.gaps == ()


class TestCapabilityGaps:
    """Out-of-envelope authored terms become bounded capability gaps."""

    def test_unsupported_os_family_is_not_realizable(self, tmp_path: Path) -> None:
        path = _variant(tmp_path, replace=("os: linux", "os: freebsd"))
        assessment = assess_scenario_capability(path)

        assert assessment.outcome is RealizabilityOutcome.NOT_REALIZABLE
        assert assessment.gaps, "an unsupported os_family must produce a gap"
        codes = {gap.code for gap in assessment.gaps}
        assert any("os-family" in code for code in codes), codes

    def test_gaps_carry_category_address_and_message(self, tmp_path: Path) -> None:
        path = _variant(tmp_path, replace=("os: linux", "os: freebsd"))
        gap = assess_scenario_capability(path).gaps[0]

        assert gap.category is GapCategory.CAPABILITY
        assert gap.code and gap.address and gap.message
        # The author needs to know which resource is at fault.
        assert isinstance(gap.address, str)

    def test_gaps_are_deduplicated_and_ordered(self, tmp_path: Path) -> None:
        path = _variant(tmp_path, replace=("os: linux", "os: freebsd"))
        gaps = assess_scenario_capability(path).gaps

        keys = [(gap.code, gap.address) for gap in gaps]
        assert len(keys) == len(set(keys)), "gaps must be deduplicated"
        assert keys == sorted(keys), "gaps must be deterministically ordered"


class TestIndeterminate:
    """Inability to assess is never reported as realizable."""

    def test_unparseable_scenario_is_indeterminate(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.sdl.yaml"
        path.write_text("nodes: [this is not: a valid scenario\n", encoding="utf-8")

        assessment = assess_scenario_capability(path)
        assert assessment.outcome is RealizabilityOutcome.INDETERMINATE
        assert assessment.outcome is not RealizabilityOutcome.REALIZABLE

    def test_missing_scenario_file_is_indeterminate(self, tmp_path: Path) -> None:
        assessment = assess_scenario_capability(tmp_path / "absent.sdl.yaml")
        assert assessment.outcome is RealizabilityOutcome.INDETERMINATE

    def test_indeterminate_carries_a_bounded_gap_without_local_paths(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.sdl.yaml"
        path.write_text("nodes: [this is not: a valid scenario\n", encoding="utf-8")

        assessment = assess_scenario_capability(path)
        assert assessment.gaps, "an indeterminate result must explain itself"
        for gap in assessment.gaps:
            assert str(tmp_path) not in gap.message, "a local filesystem path must not reach the author"


class TestImageDemands:
    """One compile feeds both contributors: capability here, supply downstream.

    The catalog layer owns the image-registry read, so the compiled plan must
    hand it a bounded demand list rather than make it re-parse the pack's SDL.
    """

    def test_authored_source_becomes_a_pinned_demand(self) -> None:
        demands = assess_scenario_capability(_MINIMAL).image_demands
        assert len(demands) == 1
        demand = demands[0]
        assert demand.source_name == "alpine"
        assert demand.source_version == "3.19"
        assert demand.os_family == "linux"
        assert demand.address == "provision.node.web"

    def test_source_less_node_yields_a_base_os_demand(self, tmp_path: Path) -> None:
        # A node with no authored source still needs a boot OS, so it demands a
        # base-OS mapping keyed on os_family rather than nothing at all.
        path = _variant(tmp_path, replace=('    source: {name: "alpine", version: "3.19"}\n', ""))
        demands = assess_scenario_capability(path).image_demands

        assert len(demands) == 1
        assert demands[0].source_name == ""
        assert demands[0].os_family == "linux"

    def test_demands_are_deduplicated_and_ordered(self) -> None:
        demands = assess_scenario_capability(_MINIMAL).image_demands
        assert list(demands) == sorted(set(demands))

    def test_demand_carries_only_bounded_identity_fields(self) -> None:
        demand = assess_scenario_capability(_MINIMAL).image_demands[0]
        assert set(vars(demand)) == {"address", "source_name", "source_version", "os_family"}
        assert isinstance(demand, ImageDemand)

    def test_unassessable_scenario_has_no_demands(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.sdl.yaml"
        path.write_text("nodes: [this is not: a valid scenario\n", encoding="utf-8")
        assert assess_scenario_capability(path).image_demands == ()


class TestNeverDispatches:
    """Assessment must never realize anything -- it is an authoring-time check."""

    def test_realizable_path_does_not_dispatch(self) -> None:
        # The seam builds its backend target with a port that raises on realize();
        # reaching apply() would surface here instead of provisioning a range.
        assessment = assess_scenario_capability(_MINIMAL)
        assert assessment.outcome is RealizabilityOutcome.REALIZABLE

    def test_dispatch_port_refuses_to_realize(self) -> None:
        from shared.aces.realizability import _NeverDispatchPort

        with pytest.raises(AssertionError):
            _NeverDispatchPort().realize({"any": "plan"})
