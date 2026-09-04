"""Tests for the superuser-only quota policy Django-admin escape hatch (PLAT-239)."""

from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from workspaces.admin import WorkspaceQuotaPolicyAdmin
from workspaces.models import WorkspaceQuotaPolicy


def _admin() -> WorkspaceQuotaPolicyAdmin:
    return WorkspaceQuotaPolicyAdmin(WorkspaceQuotaPolicy, AdminSite())


def test_identity_fields_are_editable_on_add():
    request = RequestFactory().get("/admin/")
    readonly = _admin().get_readonly_fields(request, None)
    assert "workspace" not in readonly
    assert "resource" not in readonly


def test_identity_fields_are_immutable_on_change():
    # save_model upserts by (workspace, resource); editing them on an existing row
    # would silently target a different policy, so they must be read-only on change.
    request = RequestFactory().get("/admin/")
    readonly = _admin().get_readonly_fields(request, WorkspaceQuotaPolicy())
    assert "workspace" in readonly
    assert "resource" in readonly
