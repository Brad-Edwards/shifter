"""Tests for the ACES-native launch service + dispatcher (#1479).

Covers create_aces_native_range (flag gate, CMS bookkeeping, active-range
admission, launchability, failure handling) and create_range_dispatch routing.
One integration test drives the real resolve -> load -> plan -> apply -> port
chain with only the engine seam (engine.services.create_aces_range) mocked, so
no real provisioning is triggered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cms.exceptions import CMSError
from cms.models import AcesPackageSource, RangeInstance
from cms.services import create_aces_native_range, create_range_dispatch
from shared.enums import ResourceStatus

_FIXTURES = Path(__file__).parent.parent / "shared" / "aces" / "fixtures" / "launchable"
_MINIMAL_REF = "shifter-launch-min.sdl.yaml"
_DISPATCH = "cms.services._aces_range_create._dispatch_aces_package"


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create(username="aces-launcher")


@pytest.fixture
def native_on(monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "ACES_NATIVE_PROVISIONING_ENABLED", True)


def _make_source(user, scenario_id="aces-launch", **overrides):
    fields = {
        "scenario_id": scenario_id,
        "contract_kind": "aces",
        "contract_profile": "shifter",
        "source_kind": "repo",
        "package_ref": _MINIMAL_REF,
        "package_version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        "conformance_status": "passed",
        "registered_by": user,
    }
    fields.update(overrides)
    return AcesPackageSource.objects.create(**fields)


@pytest.mark.django_db
def test_flag_off_refuses(user, monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "ACES_NATIVE_PROVISIONING_ENABLED", False)
    with pytest.raises(CMSError):
        create_aces_native_range(user, "aces-launch")


@pytest.mark.django_db
def test_launch_persists_bookkeeping_and_dispatches(user, native_on, monkeypatch):
    _make_source(user)
    seen = {}
    monkeypatch.setattr(_DISPATCH, lambda request_id, u, package_ref: seen.update(ref=package_ref))
    ctx = create_aces_native_range(user, "aces-launch")

    assert ctx.request_id is not None
    assert seen["ref"] == _MINIMAL_REF
    instance = RangeInstance.objects.get(request__request_id=ctx.request_id)
    assert instance.scenario_id == "aces-launch"
    assert instance.range_spec is None  # no cyberscript RangeSpec for ACES
    assert instance.status == ResourceStatus.PROVISIONING.value


@pytest.mark.django_db
def test_dispatch_failure_marks_failed_and_raises(user, native_on, monkeypatch):
    _make_source(user)

    def boom(*_args, **_kwargs):
        raise CMSError("dispatch failed")

    monkeypatch.setattr(_DISPATCH, boom)
    with pytest.raises(CMSError):
        create_aces_native_range(user, "aces-launch")
    # FAILED is terminal and soft-deletes the row, so query via all_objects.
    instance = RangeInstance.all_objects.get(scenario_id="aces-launch")
    assert instance.status == ResourceStatus.FAILED.value


@pytest.mark.django_db
def test_non_launchable_pending_refused(user, native_on, monkeypatch):
    _make_source(user, conformance_status="pending")
    monkeypatch.setattr(_DISPATCH, lambda *a, **k: None)
    with pytest.raises(CMSError):
        create_aces_native_range(user, "aces-launch")


@pytest.mark.django_db
def test_active_range_refused(user, native_on, monkeypatch):
    _make_source(user)
    monkeypatch.setattr(_DISPATCH, lambda *a, **k: None)
    create_aces_native_range(user, "aces-launch")
    with pytest.raises(CMSError):
        create_aces_native_range(user, "aces-launch")


@pytest.mark.django_db
def test_dispatch_routes_cyberscript_when_flag_off(user, monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "ACES_NATIVE_PROVISIONING_ENABLED", False)
    routed = {}
    monkeypatch.setattr(
        "cms.services._aces_range_create.create_range",
        lambda *a, **k: routed.setdefault("path", "cyberscript"),
    )
    create_range_dispatch(user, "basic", {})
    assert routed["path"] == "cyberscript"


@pytest.mark.django_db
def test_dispatch_routes_native_for_aces_when_flag_on(user, native_on, monkeypatch):
    _make_source(user, scenario_id="aces-x")
    routed = {}
    monkeypatch.setattr(
        "cms.services._aces_range_create.create_aces_native_range",
        lambda u, s, *, range_source=None: routed.setdefault("scenario", s),
    )
    create_range_dispatch(user, "aces-x", {})
    assert routed["scenario"] == "aces-x"


@pytest.mark.django_db
def test_dispatch_routes_cyberscript_for_non_aces_when_flag_on(user, native_on, monkeypatch):
    routed = {}
    monkeypatch.setattr(
        "cms.services._aces_range_create.create_range",
        lambda *a, **k: routed.setdefault("path", "cyberscript"),
    )
    create_range_dispatch(user, "basic", {})
    assert routed["path"] == "cyberscript"


@pytest.mark.django_db
def test_end_to_end_chain_with_engine_seam_mocked(user, native_on, monkeypatch):
    # Real resolve -> load SDL -> plan -> apply -> CmsAcesDispatchPort.realize;
    # only the engine service is mocked so no real provisioning is dispatched.
    from django.conf import settings

    from engine.services import AcesRangeRef

    monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(_FIXTURES))
    captured = {}

    def fake_create_aces_range(*, request_id, user_id, compiled_plan):
        captured["kind"] = compiled_plan.get("kind")
        captured["request_id"] = request_id
        return AcesRangeRef(request_id=request_id, accepted=True, status="accepted", range_id="rng-1")

    monkeypatch.setattr("cms.aces.dispatch.create_aces_range", fake_create_aces_range)
    _make_source(user)
    ctx = create_aces_native_range(user, "aces-launch")

    assert ctx.request_id is not None
    assert captured["kind"] == "aces_provisioning_plan"
    assert captured["request_id"] == str(ctx.request_id)
    assert RangeInstance.objects.get(request__request_id=ctx.request_id).status == ResourceStatus.PROVISIONING.value
