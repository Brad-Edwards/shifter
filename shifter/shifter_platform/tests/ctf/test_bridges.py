"""Tests for CTF -> CMS bridge scenario selection (launchability filtering)."""

import pytest
from django.contrib.auth import get_user_model

from cms.models import RaesPackageSource
from ctf import bridges

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="ctf-bridge@example.com", email="ctf-bridge@example.com")


def _make_raes(user, scenario_id, **overrides):
    fields = {
        "scenario_id": scenario_id,
        "contract_kind": "raes",
        "contract_profile": "shifter",
        "package_ref": "scenario-dev/polaris/content-packages/polaris",
        "package_version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        "registered_by": user,
    }
    fields.update(overrides)
    return RaesPackageSource.objects.create(**fields)


class TestCmsListScenariosLaunchability:
    def test_excludes_non_launchable_raes_but_keeps_legacy(self, user):
        # No runtime adapter is wired, so all RAES entries are review-only and
        # excluded from CTF event selection; legacy scenarios remain selectable.
        _make_raes(user, "polaris-ok", conformance_status="passed")
        _make_raes(user, "polaris-pending", conformance_status="pending")

        ids = {sid for sid, _ in bridges.cms_list_scenarios(user)}

        assert "basic" in ids  # legacy YAML default stays selectable
        assert "polaris-ok" not in ids  # review-only until an adapter exists
        assert "polaris-pending" not in ids  # non-launchable RAES entry excluded

    def test_includes_launchable_raes_with_adapter(self, user, monkeypatch):
        from django.conf import settings

        monkeypatch.setattr(settings, "RAES_NATIVE_PROVISIONING_ENABLED", True)
        _make_raes(user, "polaris-ok", conformance_status="passed")
        _make_raes(user, "polaris-pending", conformance_status="pending")

        ids = {sid for sid, _ in bridges.cms_list_scenarios(user)}

        assert "polaris-ok" in ids
        assert "polaris-pending" not in ids
