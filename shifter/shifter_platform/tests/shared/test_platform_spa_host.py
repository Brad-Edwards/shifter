"""Tests for the flag-gated platform SPA host view + routing (#1369).

The unified platform shell serves the SPA-owned page paths (site root and the
rehomed Risk Register routes) when ``PLATFORM_SPA_ENABLED`` is on, and preserves
the legacy Django pages when off (rollback is a flag flip).
"""

from __future__ import annotations

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

ROOT_URL = "/"
RR_LIST_URL = "/risk-register/"
ALLOWED_GROUPS = ["security"]


@pytest.fixture(autouse=True)
def _allowed_groups(settings):
    settings.RISK_REGISTER_ALLOWED_COGNITO_GROUPS = ALLOWED_GROUPS


@pytest.fixture
def member(django_user_model):
    from management.services import get_user_profile

    user = django_user_model.objects.create_user(
        username="member",
        email="member@example.com",
        password="pw",
        is_staff=True,
    )
    # Grant risk-register access so the flag-off legacy Django page renders
    # (the legacy view enforces group access); the shell layer never does.
    profile = get_user_profile(user)
    profile.cognito_groups = list(ALLOWED_GROUPS)
    profile.save(update_fields=["cognito_groups"])
    return user


@pytest.fixture
def spa_on(settings):
    settings.PLATFORM_SPA_ENABLED = True
    settings.RISK_REGISTER_SPA_ENABLED = False


@pytest.fixture
def spa_off(settings):
    settings.PLATFORM_SPA_ENABLED = False
    settings.RISK_REGISTER_SPA_ENABLED = False


class TestPlatformSpaEnabled:
    def test_root_serves_shell_and_primes_csrf(self, spa_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get(ROOT_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content
        assert "csrftoken" in resp.cookies

    def test_root_anonymous_redirects_to_login(self, spa_on):
        resp = Client().get(ROOT_URL)
        assert resp.status_code == 302

    def test_risk_register_served_by_platform_shell(self, spa_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get(RR_LIST_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_risk_register_client_deep_link_serves_shell(self, spa_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get("/risk-register/risks/999/")
        assert resp.status_code == 200
        assert b'id="root"' in resp.content


class TestPlatformSpaDisabled:
    def test_root_serves_legacy_landing_not_shell(self, spa_off):
        # Root landing is public when the SPA is off; it must not be the shell.
        resp = Client().get(ROOT_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content

    def test_risk_register_serves_django_page_not_shell(self, spa_off, member):
        client = Client()
        client.force_login(member)
        resp = client.get(RR_LIST_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content


def test_legacy_risk_register_flag_still_serves_shell(settings, member):
    # An in-flight deploy toggled on the older flag keeps working.
    settings.PLATFORM_SPA_ENABLED = False
    settings.RISK_REGISTER_SPA_ENABLED = True
    client = Client()
    client.force_login(member)
    resp = client.get(RR_LIST_URL)
    assert resp.status_code == 200
    assert b'id="root"' in resp.content
