"""Tests for the flag-gated CTF participant workspace SPA host + routing (#1372).

Mirrors ``tests/scenario_editor/test_spa_host.py``. The CTF carve-out requires
BOTH ``PLATFORM_SPA_ENABLED`` and ``CTF_WORKSPACE_SPA_ENABLED`` (the per-surface
extension of the platform flag pattern); either flag alone must not enable the
SPA shell for participant pages. Because participant and organizer share the
``/ctf/`` prefix, the deep-link catch-all is scoped with a negative lookahead:
the organizer (``/ctf/admin/*``) pages, the participant login / change-password /
team-join Django views, and the legacy scoreboard JSON endpoint are NEVER
shell-served regardless of the flag state.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import resolve

from ctf import views

pytestmark = pytest.mark.django_db

DASHBOARD_URL = "/ctf/"
CHALLENGES_URL = "/ctf/challenges/"
SCOREBOARD_URL = "/ctf/scoreboard/"
TEAM_URL = "/ctf/team/"
RANGE_URL = "/ctf/range/"
HELP_URL = "/ctf/help/"
ADMIN_URL = "/ctf/admin/"
LOGIN_URL = "/ctf/login/"


@pytest.fixture
def spa_on(settings):
    settings.PLATFORM_SPA_ENABLED = True
    settings.CTF_WORKSPACE_SPA_ENABLED = True


@pytest.fixture
def spa_off(settings):
    settings.PLATFORM_SPA_ENABLED = False
    settings.CTF_WORKSPACE_SPA_ENABLED = False


class TestSpaEnabled:
    def test_dashboard_serves_shell_and_primes_csrf(self, spa_on, authenticated_participant_client):
        resp = authenticated_participant_client.get(DASHBOARD_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content
        assert "csrftoken" in resp.cookies

    @pytest.mark.parametrize("url", [CHALLENGES_URL, SCOREBOARD_URL, TEAM_URL, RANGE_URL, HELP_URL, "/ctf/event/"])
    def test_participant_pages_serve_shell(self, spa_on, authenticated_participant_client, url):
        resp = authenticated_participant_client.get(url)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_challenge_detail_deep_link_serves_shell(self, spa_on, authenticated_participant_client):
        # Client-routed even for a challenge id that does not exist server-side.
        resp = authenticated_participant_client.get(f"/ctf/challenges/{uuid4()}/")
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_client_router_catchall_serves_shell(self, spa_on, authenticated_participant_client):
        # A participant client sub-route with no exact server route resolves to
        # the shell via the scoped catch-all.
        resp = authenticated_participant_client.get(f"/ctf/challenges/{uuid4()}/notes/")
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_shell_served_without_participant_role(self, spa_on, authenticated_standard_client):
        # The shell host is thin-auth (authenticated only); per-surface access is
        # enforced by the /api/v1/ctf/ endpoints the SPA calls. Any authenticated
        # user gets the shell markup.
        resp = authenticated_standard_client.get(CHALLENGES_URL)
        assert resp.status_code == 200
        assert b'id="root"' in resp.content

    def test_anonymous_redirects_to_login(self, spa_on):
        resp = Client().get(DASHBOARD_URL)
        assert resp.status_code == 302

    def test_post_falls_through_to_django_when_enabled(self, spa_on, ctf_participant, authenticated_participant_client):
        # The shell (require_safe) must not take over unsafe methods; a POST must
        # reach the Django view, not the shell (the SPA mutates via /api/v1/ctf/).
        resp = authenticated_participant_client.post(DASHBOARD_URL, {})
        assert b'id="root"' not in resp.content

    def test_admin_pages_never_shell_served(self, spa_on, authenticated_organizer_client):
        # The catch-all excludes /ctf/admin/*, so organizer pages keep serving
        # their Django views even with the participant SPA flags on.
        resp = authenticated_organizer_client.get(ADMIN_URL)
        assert b'id="root"' not in resp.content

    def test_admin_deep_link_not_swallowed_by_catchall(self, spa_on, authenticated_organizer_client):
        # A bogus admin path must 404 (Django), never be served the participant
        # shell by the catch-all.
        resp = authenticated_organizer_client.get("/ctf/admin/does-not-exist/")
        assert resp.status_code == 404
        assert b'id="root"' not in resp.content

    def test_login_never_shell_served(self, spa_on):
        resp = Client().get(LOGIN_URL)
        assert b'id="root"' not in resp.content


class TestSpaDisabled:
    def test_dashboard_serves_django_page_not_shell(self, spa_off, ctf_participant, authenticated_participant_client):
        resp = authenticated_participant_client.get(DASHBOARD_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content

    def test_client_router_catchall_404s(self, spa_off, authenticated_participant_client):
        resp = authenticated_participant_client.get(f"/ctf/challenges/{uuid4()}/notes/")
        assert resp.status_code == 404


class TestSpaRequiresBothFlags:
    def test_platform_flag_alone_does_not_enable_ctf_spa(
        self, ctf_participant, authenticated_participant_client, settings
    ):
        settings.PLATFORM_SPA_ENABLED = True
        settings.CTF_WORKSPACE_SPA_ENABLED = False
        resp = authenticated_participant_client.get(DASHBOARD_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content

    def test_ctf_flag_alone_does_not_enable_without_platform_flag(
        self, ctf_participant, authenticated_participant_client, settings
    ):
        settings.PLATFORM_SPA_ENABLED = False
        settings.CTF_WORKSPACE_SPA_ENABLED = True
        resp = authenticated_participant_client.get(DASHBOARD_URL)
        assert resp.status_code == 200
        assert b'id="root"' not in resp.content


def test_participant_auth_and_admin_routes_stay_django():
    """The participant login / change-password / team-join Django views, the
    legacy scoreboard JSON endpoint, and the organizer pages keep serving their
    real views regardless of the SPA flags (never wrapped by ``_page``)."""
    assert resolve("/ctf/login/").func is views.ctf_login
    assert resolve("/ctf/change-password/").func is views.ctf_change_password
    assert resolve("/ctf/team/join/").func is views.team_join
    assert resolve("/ctf/admin/").func is views.admin_dashboard
    assert resolve(f"/ctf/api/events/{uuid4()}/scoreboard/").func is views.api_scoreboard
