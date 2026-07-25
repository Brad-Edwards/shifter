"""Tests for the read-only ``shared`` admin registrations (#1374).

Restores the coverage ``tests/risk_register/test_admin.py`` provided for
``APIKeyAdmin`` before ``risk_register`` was deleted, and extends it to
``AuditLogAdmin`` (whose read-only overrides had never been exercised). Both
admins guard immutable security/evidence records — staff must never be able
to create, hand-edit, or hard-delete an ``AuditLog`` row or an archival
``APIKey`` row through ``/admin/`` — so this drives real admin add/change/
delete requests (the strongest boundary that actually exercises the
restriction), in addition to asserting the ``ModelAdmin`` permission methods
directly.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site

from shared.admin import APIKeyAdmin, AuditLogAdmin
from shared.models import APIKey, AuditLog

pytestmark = pytest.mark.django_db

AUDITLOG_ADD_URL = "/admin/shared/auditlog/add/"
APIKEY_ADD_URL = "/admin/shared/apikey/add/"


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin-readonly-test",
        email="admin-readonly-test@example.com",
        password="pw",
    )


@pytest.fixture
def admin_client(client, superuser):
    client.force_login(superuser)
    return client


@pytest.fixture
def audit_row():
    return AuditLog.objects.create(entity_type="range", entity_id=1, action="create", actor_type="system")


@pytest.fixture
def api_key_row():
    return APIKey.objects.create(name="legacy", prefix="lgcy0001", key_hash="a" * 64)


class TestAuditLogAdminIsReadOnly:
    def test_permission_methods_are_false(self, audit_row):
        model_admin = AuditLogAdmin(AuditLog, site)
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_change_permission(None, audit_row) is False
        assert model_admin.has_delete_permission(None) is False
        assert model_admin.has_delete_permission(None, audit_row) is False

    def test_add_view_is_forbidden(self, admin_client):
        resp = admin_client.get(AUDITLOG_ADD_URL)
        assert resp.status_code == 403
        assert AuditLog.objects.count() == 0

    def test_change_view_rejects_edits(self, admin_client, audit_row):
        resp = admin_client.post(
            f"/admin/shared/auditlog/{audit_row.pk}/change/",
            {"entity_type": "user", "entity_id": 999, "action": "delete", "actor_type": "system"},
        )
        assert resp.status_code == 403
        audit_row.refresh_from_db()
        assert audit_row.entity_type == "range"
        assert audit_row.entity_id == 1

    def test_delete_view_is_forbidden_and_row_survives(self, admin_client, audit_row):
        resp = admin_client.post(f"/admin/shared/auditlog/{audit_row.pk}/delete/")
        assert resp.status_code == 403
        assert AuditLog.objects.filter(pk=audit_row.pk).exists()


class TestAPIKeyAdminIsReadOnly:
    def test_permission_methods_are_false(self, api_key_row):
        model_admin = APIKeyAdmin(APIKey, site)
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_change_permission(None, api_key_row) is False
        assert model_admin.has_delete_permission(None) is False
        assert model_admin.has_delete_permission(None, api_key_row) is False

    def test_add_view_is_forbidden(self, admin_client):
        resp = admin_client.get(APIKEY_ADD_URL)
        assert resp.status_code == 403
        assert APIKey.objects.count() == 0

    def test_change_view_rejects_edits(self, admin_client, api_key_row):
        resp = admin_client.post(
            f"/admin/shared/apikey/{api_key_row.pk}/change/",
            {"name": "renamed", "prefix": "lgcy0001", "key_hash": "b" * 64},
        )
        assert resp.status_code == 403
        api_key_row.refresh_from_db()
        assert api_key_row.name == "legacy"
        assert api_key_row.key_hash == "a" * 64

    def test_delete_view_is_forbidden_and_row_survives(self, admin_client, api_key_row):
        resp = admin_client.post(f"/admin/shared/apikey/{api_key_row.pk}/delete/")
        assert resp.status_code == 403
        assert APIKey.objects.filter(pk=api_key_row.pk).exists()
