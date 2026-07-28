"""Read-only RAES catalog presentation in the scenario editor (issue #1254)."""

from __future__ import annotations

import pytest

from cms.models import RaesPackageSource, ScenarioMetadata

# staff_user / staff_client / regular_client / threat_research_client come from conftest.py

VIEW_BASE = "/scenario-editor/"

pytestmark = pytest.mark.django_db


def _make_raes_source(staff_user, scenario_id="polaris-raes", **overrides):
    fields = {
        "scenario_id": scenario_id,
        "contract_kind": "raes",
        "contract_profile": "shifter",
        "package_ref": "scenario-dev/polaris/content-packages/polaris",
        "package_version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        "conformance_status": "passed",
        "conformance_report_ref": "reports/polaris-conformance.json",
        "provenance": {"repo": "acme/raes", "commit": "c" * 40},
        "registered_by": staff_user,
    }
    fields.update(overrides)
    return RaesPackageSource.objects.create(**fields)


class TestRaesDetailPresentation:
    def test_raes_detail_renders_read_only_metadata(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.get(f"{VIEW_BASE}polaris-raes/")

        assert response.status_code == 200
        body = response.content.decode()
        assert "polaris-raes" in body
        assert "shifter" in body  # contract_profile
        assert "sha256:" + "a" * 64 in body  # package_digest

    def test_raes_detail_omits_authoring_actions(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.get(f"{VIEW_BASE}polaris-raes/")

        body = response.content.decode()
        assert f"{VIEW_BASE}polaris-raes/edit/" not in body
        assert f"{VIEW_BASE}polaris-raes/editor/" not in body
        assert f"{VIEW_BASE}polaris-raes/clone/" not in body
        assert f"{VIEW_BASE}polaris-raes/delete/" not in body
        assert f"{VIEW_BASE}polaris-raes/export/" not in body

    def test_raes_detail_does_not_leak_raw_provenance_object(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.get(f"{VIEW_BASE}polaris-raes/")

        body = response.content.decode()
        # Bounded summary values are shown; internal model attrs are not dumped.
        assert "registered_by" not in body


class TestRaesListPresentation:
    def test_raes_row_has_view_but_no_authoring_actions(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.get(VIEW_BASE)

        body = response.content.decode()
        assert f"{VIEW_BASE}polaris-raes/edit/" not in body
        assert f"{VIEW_BASE}polaris-raes/clone/" not in body
        assert f"{VIEW_BASE}polaris-raes/delete/" not in body
        assert f"{VIEW_BASE}polaris-raes/export/" not in body
        # The read-only detail link is still present.
        assert f"{VIEW_BASE}polaris-raes/" in body

    def test_legacy_row_keeps_authoring_actions(self, staff_client, staff_user, valid_definition):
        from cms.models import Scenario

        Scenario.objects.create(
            scenario_id="legacy-custom",
            name="Legacy Custom",
            description="legacy",
            definition=valid_definition,
            created_by=staff_user,
            updated_by=staff_user,
        )

        response = staff_client.get(VIEW_BASE)

        body = response.content.decode()
        assert f"{VIEW_BASE}legacy-custom/clone/" in body
        assert f"{VIEW_BASE}legacy-custom/export/" in body


class TestRaesMetadataToggle:
    def test_staff_can_toggle_raes_enabled(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.post(f"{VIEW_BASE}polaris-raes/toggle-enabled/")

        assert response.status_code == 302
        meta = ScenarioMetadata.objects.get(scenario_id="polaris-raes")
        assert meta.enabled is False

    def test_staff_can_toggle_raes_staff_only(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.post(f"{VIEW_BASE}polaris-raes/toggle-staff-only/")

        assert response.status_code == 302
        meta = ScenarioMetadata.objects.get(scenario_id="polaris-raes")
        assert meta.staff_only is True


class TestLegacyDetailUnchanged:
    def test_legacy_default_detail_still_renders_yaml(self, staff_client):
        response = staff_client.get(f"{VIEW_BASE}basic/")

        assert response.status_code == 200
        body = response.content.decode()
        assert "YAML Definition" in body
