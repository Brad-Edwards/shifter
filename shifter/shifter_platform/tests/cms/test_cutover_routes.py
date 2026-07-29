"""Tests for the ADR-031-R6 catalog source-route overlay + resolution (#1310).

The registry exclusively owns the ``public_id -> source_id`` route: a routed
public id (``polaris``) becomes the ACES-backed launch choice keeping its public
identity/access, its internal source id (``polaris-aces``) is suppressed as a
second launch choice, and a dangling/non-conformant target fails closed (the
public id is non-launchable, never silent legacy). ``resolve_launch`` is the one
resolution both the projection and dispatch consume.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from cms.models import AcesPackageSource
from cms.scenarios.cutover import resolve_launch
from cms.scenarios.registry import list_all_scenarios

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(username="cutover@example.com", email="cutover@example.com", is_staff=True)


def _make_source(staff_user, *, scenario_id="polaris-aces", conformance_status="passed", **overrides):
    fields = {
        "scenario_id": scenario_id,
        "source_kind": "repo",
        "contract_kind": "aces",
        "contract_profile": "shifter",
        "package_ref": "scenario-dev/polaris/content-packages/polaris",
        "package_version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        "conformance_status": conformance_status,
        "registered_by": staff_user,
    }
    fields.update(overrides)
    return AcesPackageSource.objects.create(**fields)


def _route(settings):
    settings.ACES_NATIVE_PROVISIONING_ENABLED = True
    settings.ACES_CATALOG_CUTOVERS = {"polaris": "polaris-aces"}


def _entry(scenario_id):
    return next((e for e in list_all_scenarios(user=None) if e["id"] == scenario_id), None)


class TestCatalogOverlay:
    def test_routed_public_is_aces_backed_and_target_suppressed(self, staff_user, settings):
        _make_source(staff_user)
        _route(settings)
        polaris = _entry("polaris")
        assert polaris is not None
        assert polaris["scenario_type"] == "aces"
        assert polaris["launchable"] is True
        # The internal source id is not offered as a second launch choice.
        assert _entry("polaris-aces") is None

    def test_public_keeps_its_display_and_access(self, staff_user, settings):
        _make_source(staff_user)
        _route(settings)
        polaris = _entry("polaris")
        # Public id/name preserved (not renamed to the internal source id).
        assert polaris["id"] == "polaris"
        assert polaris["name"] and polaris["name"] != "polaris-aces"

    def test_route_to_unregistered_target_fails_closed(self, settings):
        _route(settings)  # no polaris-aces row registered
        polaris = _entry("polaris")
        assert polaris["scenario_type"] == "aces"
        assert polaris["launchable"] is False  # never silent legacy fallback
        assert _entry("polaris-aces") is None

    def test_route_to_nonconformant_target_fails_closed(self, staff_user, settings):
        _make_source(staff_user, conformance_status="pending")
        _route(settings)
        polaris = _entry("polaris")
        assert polaris["launchable"] is False
        assert _entry("polaris-aces") is None  # still suppressed while routed

    def test_empty_route_leaves_legacy_polaris_and_visible_source(self, staff_user, settings):
        _make_source(staff_user)
        settings.ACES_CATALOG_CUTOVERS = {}
        polaris = _entry("polaris")
        assert polaris["launchable"] is True  # legacy polaris still launchable
        assert polaris.get("scenario_type") != "aces"
        # Unrouted ACES source is visible under its own id.
        assert _entry("polaris-aces") is not None


class TestResolveLaunch:
    def test_routed_public_resolves_to_internal_source(self, staff_user, settings):
        _make_source(staff_user)
        _route(settings)
        res = resolve_launch("polaris")
        assert res.is_aces is True
        assert res.aces_source_id == "polaris-aces"
        assert res.scenario_id == "polaris"
        assert res.launchable is True

    def test_routed_target_is_not_a_direct_launch_choice(self, staff_user, settings):
        _make_source(staff_user)
        _route(settings)
        res = resolve_launch("polaris-aces")
        assert res.is_aces is True
        assert res.aces_source_id is None
        assert res.launchable is False

    def test_legacy_scenario_resolves_to_legacy(self, settings):
        settings.ACES_CATALOG_CUTOVERS = {}
        res = resolve_launch("basic")
        assert res.is_aces is False
        assert res.aces_source_id is None
        assert res.launchable is True

    def test_unrouted_registered_source_launches_directly(self, staff_user, settings):
        _make_source(staff_user, scenario_id="standalone-aces")
        settings.ACES_NATIVE_PROVISIONING_ENABLED = True
        settings.ACES_CATALOG_CUTOVERS = {}
        res = resolve_launch("standalone-aces")
        assert res.is_aces is True
        assert res.aces_source_id == "standalone-aces"
