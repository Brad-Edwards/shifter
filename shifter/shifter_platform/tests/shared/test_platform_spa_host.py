"""Tests for the flag-gated platform SPA host view + routing (#1369).

The unified platform shell serves SPA-owned page paths when
``PLATFORM_SPA_ENABLED`` is on.
"""

from __future__ import annotations

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

ROOT_URL = "/"


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user(
        username="member",
        email="member@example.com",
        password="pw",
        is_staff=True,
    )


@pytest.fixture
def spa_on(settings):
    settings.PLATFORM_SPA_ENABLED = True


@pytest.fixture
def spa_off(settings):
    settings.PLATFORM_SPA_ENABLED = False


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


class TestPlatformSpaDisabled:
    def test_root_serves_legacy_landing_not_shell(self, spa_off):
        # Root landing is public when the SPA is off; it must not be the shell.
        resp = Client().get(ROOT_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content


RAES_IMG_URL = "/raes-image-registry/"


@pytest.fixture
def raes_native_on(settings):
    settings.PLATFORM_SPA_ENABLED = True
    settings.RAES_NATIVE_PROVISIONING_ENABLED = True


class TestRaesImageRegistrySpaHost:
    """The greenfield RAES image registry pages (#1566) require both flags."""

    def test_served_by_shell_when_both_flags_on(self, raes_native_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get(RAES_IMG_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_client_deep_link_serves_shell(self, raes_native_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get("/raes-image-registry/anything/")
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_404_when_native_flag_off(self, settings, member):
        settings.PLATFORM_SPA_ENABLED = True
        settings.RAES_NATIVE_PROVISIONING_ENABLED = False
        client = Client()
        client.force_login(member)
        assert client.get(RAES_IMG_URL).status_code == 404

    def test_404_when_platform_spa_off(self, settings, member):
        settings.PLATFORM_SPA_ENABLED = False
        settings.RAES_NATIVE_PROVISIONING_ENABLED = True
        client = Client()
        client.force_login(member)
        assert client.get(RAES_IMG_URL).status_code == 404

    def test_anonymous_redirects_to_login_when_enabled(self, raes_native_on):
        resp = Client().get(RAES_IMG_URL)
        assert resp.status_code == 302


ADMINISTER_URL = "/administer/"
DJANGO_ADMIN_URL = "/admin/"


@pytest.fixture
def administer_on(settings):
    settings.PLATFORM_SPA_ENABLED = True
    settings.ADMINISTER_SPA_ENABLED = True


class TestAdministerSpaHost:
    """The greenfield Administer workspace pages (#1373) require both flags."""

    def test_served_by_shell_when_both_flags_on(self, administer_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get(ADMINISTER_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_client_deep_link_serves_shell(self, administer_on, member):
        client = Client()
        client.force_login(member)
        resp = client.get("/administer/users/42/")
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_404_when_administer_flag_off(self, settings, member):
        settings.PLATFORM_SPA_ENABLED = True
        settings.ADMINISTER_SPA_ENABLED = False
        client = Client()
        client.force_login(member)
        assert client.get(ADMINISTER_URL).status_code == 404

    def test_404_when_platform_spa_off(self, settings, member):
        settings.PLATFORM_SPA_ENABLED = False
        settings.ADMINISTER_SPA_ENABLED = True
        client = Client()
        client.force_login(member)
        assert client.get(ADMINISTER_URL).status_code == 404

    def test_anonymous_redirects_to_login_when_enabled(self, administer_on):
        resp = Client().get(ADMINISTER_URL)
        assert resp.status_code == 302

    def test_django_admin_unaffected_by_flag(self, administer_on, member):
        # /admin/ stays mapped to Django admin in every rollout state and is never
        # captured by the SPA shell.
        client = Client()
        client.force_login(member)
        resp = client.get(DJANGO_ADMIN_URL, follow=True)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content
