"""Behavior tests for the ngfw_detail view in mission_control/views.

Drives the real view → real ``cms.services.get_ngfw`` (against a real CMS NGFW
``App``) → real ``engine.services.get_ranges_for_ngfw`` → the real
``ngfw/detail.html`` template, instead of patching ``cms_get_ngfw`` /
``get_ranges_for_ngfw`` / ``render``. Assertions read the rendered response.

Note (flagged separately): ``ngfw_detail`` resolves linked ranges by passing
``int(ngfw.instance_id)`` — the CMS Instance UUID coerced to its 128-bit int —
to ``get_ranges_for_ngfw``, which filters ``engine.Range.ngfw_instance_id`` (a
64-bit int FK to the *engine* Instance). Those id spaces never coincide, so the
linked-ranges table is always empty in practice; that is a product bug, not a
property to assert here. These tests therefore pin the view's render/redirect
contract, not a populated linked-ranges table.
"""

import time

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="ngfw-detail@example.com", email="ngfw-detail@example.com")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="ngfw-detail-other@example.com", email="ngfw-detail-other@example.com")


@pytest.fixture
def client_for(db):
    def _make(user):
        client = Client()
        client.force_login(user)
        session = client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()
        return client

    return _make


class TestNGFWDetailView:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "PRODUCT BUG: ngfw_detail passes int(cms NGFWAppContext.instance_id) — the CMS "
            "Instance UUID coerced to a 128-bit int — to get_ranges_for_ngfw, which filters "
            "engine.Range.ngfw_instance_id (a 64-bit int FK to the *engine* NGFW Instance). The "
            "id spaces never coincide, so the detail page raises OverflowError (HTTP 500) on "
            "SQLite and silently shows no linked ranges on Postgres. Drop this xfail once the "
            "cms<->engine NGFW-instance linkage is fixed."
        ),
    )
    def test_renders_detail_for_owned_ngfw(self, user, client_for, cms_ngfw_app):
        app = cms_ngfw_app(user, name="DevNGFW")
        client = Client(raise_request_exception=False)
        client.force_login(user)
        session = client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

        response = client.get(reverse("mission_control:ngfw_detail", kwargs={"app_id": str(app.id)}))

        # Correct behavior once the linkage bug is fixed: the page renders with
        # the NGFW name (and an empty linked-ranges section for a fresh NGFW).
        assert response.status_code == 200
        assert "DevNGFW" in response.content.decode()

    def test_redirects_to_list_when_ngfw_missing(self, user, client_for):
        from uuid import uuid4

        response = client_for(user).get(reverse("mission_control:ngfw_detail", kwargs={"app_id": str(uuid4())}))

        assert response.status_code == 302
        assert reverse("mission_control:ngfw_list") in response.url

    def test_redirects_when_ngfw_owned_by_other_user(self, user, other_user, client_for, cms_ngfw_app):
        app = cms_ngfw_app(other_user, name="NotYours")

        response = client_for(user).get(reverse("mission_control:ngfw_detail", kwargs={"app_id": str(app.id)}))

        assert response.status_code == 302
        assert reverse("mission_control:ngfw_list") in response.url
