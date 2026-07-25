"""Tests for the ``shared.AuditLog`` model (#1374 rehome from risk_register).

``AuditLog`` moved from ``risk_register.models`` to ``shared.models`` so the
durable audit store is owned by the ``shared`` app rather than a feature
domain slated for removal (ADR-001). The table name changes from
``risk_register_auditlog`` to ``shared_auditlog``.
"""

from __future__ import annotations

import pytest
from django.apps import apps

from shared.models import AuditLog

pytestmark = pytest.mark.django_db


def test_auditlog_resolves_from_shared_app() -> None:
    """``shared.AuditLog`` is registered in the ``shared`` app's model registry."""
    model = apps.get_model("shared", "AuditLog")
    assert model is AuditLog


def test_auditlog_table_is_shared_auditlog() -> None:
    """The durable table is named ``shared_auditlog``, not the old risk_register name."""
    assert AuditLog._meta.db_table == "shared_auditlog"


def test_auditlog_create_and_query_roundtrip() -> None:
    """Rows persist and are queryable from the rehomed shared table."""
    row = AuditLog.objects.create(
        entity_type="range",
        entity_id=1,
        action="create",
        actor_type="system",
        context="rehome smoke test",
    )
    stored = AuditLog.objects.get(pk=row.pk)
    assert stored.entity_type == "range"
    assert stored.action == "create"
    assert stored.context == "rehome smoke test"


def test_risk_register_app_no_longer_exists() -> None:
    """``risk_register`` (and any claim it once owned ``AuditLog``) is gone entirely (#1374 Part B)."""
    with pytest.raises(LookupError):
        apps.get_app_config("risk_register")
