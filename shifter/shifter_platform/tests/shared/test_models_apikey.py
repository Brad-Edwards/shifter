"""Tests for the ``shared.APIKey`` model (#1374 Part B rehome from risk_register).

``APIKey`` moved from ``risk_register.models`` to ``shared.models`` after
``Risk``/``Comment`` (the only in-tree FKs to it) were deleted, so the move
never creates a cross-app-label FK (``management.check_model_fks``). The table
name changes from ``risk_register_apikey`` to ``shared_apikey``. The model
remains archival-only: no runtime authentication or mint path (PLAT-106 /
#1124 retired the legacy ``rr_live_`` credential).
"""

from __future__ import annotations

import pytest
from django.apps import apps

from shared.models import APIKey

pytestmark = pytest.mark.django_db


def test_apikey_resolves_from_shared_app() -> None:
    """``shared.APIKey`` is registered in the ``shared`` app's model registry."""
    model = apps.get_model("shared", "APIKey")
    assert model is APIKey


def test_apikey_table_is_shared_apikey() -> None:
    """The durable table is named ``shared_apikey``, not the old risk_register name."""
    assert APIKey._meta.db_table == "shared_apikey"


def test_apikey_create_and_query_roundtrip() -> None:
    row = APIKey.objects.create(name="ci", prefix="abcd1234", key_hash="a" * 64)
    stored = APIKey.objects.get(pk=row.pk)
    assert stored.name == "ci"
    assert stored.is_active is True


def test_apikey_is_active_false_when_revoked() -> None:
    from django.utils import timezone

    row = APIKey.objects.create(name="revoked", prefix="revk0001", key_hash="b" * 64, revoked_at=timezone.now())
    assert row.is_active is False
