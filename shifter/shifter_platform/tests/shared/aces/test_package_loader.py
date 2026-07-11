"""Tests for the ACES-native package launch loader (#1479).

Exercises the real load -> plan -> apply -> dispatch flow against a minimal
in-repo SDL fixture and a recording dispatch port (no DB/cloud). This is the
launch-side oracle for the two backend-conformance fixes #1479 depends on:
the manifest declaring ``switch`` support (so networks plan) and the provisional
snapshot echoing authored realization concerns (so the runtime non-approximation
gate passes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from shared.aces.dispatch_port import ShifterDispatchResult
from shared.aces.package_loader import (
    AcesPackageError,
    ShifterLaunchResult,
    launch_aces_package,
    resolve_scenario_path,
)
from shared.aces.runtime_target import ACES_PROVISIONING_PLAN_KIND

_FIXTURES = Path(__file__).parent / "fixtures" / "launchable"
_MINIMAL = "shifter-launch-min.sdl.yaml"


@dataclass
class _RecordingPort:
    """Recording dispatch port: no DB/cloud, returns an accepted result."""

    request_id: str = "req-launch-1"
    accepted: bool = True
    plans: list = field(default_factory=list)

    def realize(self, compiled_plan) -> ShifterDispatchResult:
        self.plans.append(compiled_plan)
        return ShifterDispatchResult(
            request_id=self.request_id,
            accepted=self.accepted,
            status="accepted" if self.accepted else "rejected",
            range_id="rng-1" if self.accepted else None,
        )


def test_resolve_scenario_path_returns_contained_file():
    path = resolve_scenario_path(_MINIMAL, package_root=_FIXTURES)
    assert path == (_FIXTURES / _MINIMAL).resolve()


def test_resolve_scenario_path_rejects_traversal():
    with pytest.raises(AcesPackageError):
        resolve_scenario_path("../../../../etc/passwd", package_root=_FIXTURES)


def test_resolve_scenario_path_rejects_missing_and_empty():
    with pytest.raises(AcesPackageError):
        resolve_scenario_path("does-not-exist.sdl.yaml", package_root=_FIXTURES)
    with pytest.raises(AcesPackageError):
        resolve_scenario_path("   ", package_root=_FIXTURES)


def test_launch_aces_package_compiles_and_dispatches():
    port = _RecordingPort()
    result = launch_aces_package(scenario_path=_FIXTURES / _MINIMAL, port=port)
    assert isinstance(result, ShifterLaunchResult)
    assert result.accepted is True
    assert result.diagnostics == ()
    # Node + network both realized (network requires the manifest 'switch' fix).
    assert any(addr.endswith("node.web") for addr in result.changed_addresses)
    assert any("network" in addr for addr in result.changed_addresses)
    # The port received exactly one serialized ACES provisioning plan.
    assert len(port.plans) == 1
    assert port.plans[0]["kind"] == ACES_PROVISIONING_PLAN_KIND


def test_launch_aces_package_rejected_dispatch_is_not_accepted():
    result = launch_aces_package(scenario_path=_FIXTURES / _MINIMAL, port=_RecordingPort(accepted=False))
    assert result.accepted is False
    assert result.status == "rejected"


def test_launch_aces_package_bad_sdl_raises_sanitized(tmp_path):
    bad = tmp_path / "broken.sdl.yaml"
    bad.write_text("name: broken\nnodes: {web: {type: NotARealType}}\n", encoding="utf-8")
    with pytest.raises(AcesPackageError):
        launch_aces_package(scenario_path=bad, port=_RecordingPort())
