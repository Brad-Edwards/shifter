"""Realizability on the Django RAES detail page (#1581, ADR-034-R3).

The SPA is flag-gated (``PLATFORM_SPA_ENABLED`` / ``SCENARIO_EDITOR_SPA_ENABLED``
both default to ``False``), so this Django template is the surface a default
deployment actually renders. ADR-034-R3 requires non-realizability to reach the
author, which means it has to appear here too -- not only in the SPA.

Both surfaces render the *same* server-projected assessment, so these tests pin
the rendering, not the assessment logic (owned by
``tests/cms/test_scenario_realizability.py``).
"""

from __future__ import annotations

import pytest

from cms.models import RaesPackageSource

pytestmark = pytest.mark.django_db

VIEW_BASE = "/scenario-editor/"


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


class TestRealizabilityIsSurfaced:
    """The author sees the assessment on the default (non-SPA) surface."""

    def test_detail_page_renders_a_realizability_section(self, staff_client, staff_user):
        _make_raes_source(staff_user)

        response = staff_client.get(f"{VIEW_BASE}polaris-raes/")

        assert response.status_code == 200
        assert "Backend Realizability" in response.content.decode()

    def test_unassessable_pack_is_not_presented_as_realizable(self, staff_client, staff_user):
        # The pack_ref does not resolve in the test environment, so the honest
        # answer is "cannot be checked" -- never a green pass.
        _make_raes_source(staff_user)

        body = staff_client.get(f"{VIEW_BASE}polaris-raes/").content.decode()

        assert "Cannot Be Checked" in body or "Not Realizable" in body

    def test_detail_page_still_renders_when_assessment_is_unavailable(self, staff_client, staff_user):
        # A failed assessment must degrade the panel, never the whole page.
        _make_raes_source(staff_user)

        response = staff_client.get(f"{VIEW_BASE}polaris-raes/")

        assert response.status_code == 200
        assert "polaris-raes" in response.content.decode()


class TestBoundedRendering:
    """Only bounded gap fields reach the template."""

    def test_no_local_paths_are_rendered(self, staff_client, staff_user, tmp_path):
        _make_raes_source(staff_user)

        body = staff_client.get(f"{VIEW_BASE}polaris-raes/").content.decode()

        assert str(tmp_path) not in body
        assert "/home/" not in body

    def test_legacy_scenario_detail_has_no_realizability_section(self, staff_client, staff_user, valid_definition):
        from cms.models import Scenario

        Scenario.objects.create(
            scenario_id="legacy-lab",
            name="Legacy Lab",
            description="legacy",
            definition=valid_definition,
            created_by=staff_user,
            updated_by=staff_user,
        )

        body = staff_client.get(f"{VIEW_BASE}legacy-lab/").content.decode()

        assert "Backend Realizability" not in body
