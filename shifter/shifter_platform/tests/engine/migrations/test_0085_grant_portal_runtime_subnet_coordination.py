"""Tests for the portal_runtime subnet-coordination EXECUTE grant (#2219)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

MIGRATION = importlib.import_module("engine.migrations.0085_grant_portal_runtime_subnet_coordination")


def _schema_editor(*, vendor: str, portal_runtime_exists: bool):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (1,) if portal_runtime_exists else None
    connection = SimpleNamespace(vendor=vendor, cursor=MagicMock(return_value=cursor))
    return SimpleNamespace(connection=connection, execute=MagicMock()), cursor


def test_postgresql_grants_execute_to_portal_runtime_when_present() -> None:
    schema_editor, cursor = _schema_editor(vendor="postgresql", portal_runtime_exists=True)

    MIGRATION.grant_portal_runtime_coordination_execute(None, schema_editor)

    schema_editor.execute.assert_called_once_with(MIGRATION._PORTAL_RUNTIME_GRANTS)
    cursor.execute.assert_called_once_with("SELECT 1 FROM pg_roles WHERE rolname = %s", ["portal_runtime"])


def test_postgresql_skips_grant_when_portal_runtime_absent() -> None:
    schema_editor, _cursor = _schema_editor(vendor="postgresql", portal_runtime_exists=False)

    MIGRATION.grant_portal_runtime_coordination_execute(None, schema_editor)

    schema_editor.execute.assert_not_called()


def test_non_postgresql_database_is_unchanged() -> None:
    schema_editor, cursor = _schema_editor(vendor="sqlite", portal_runtime_exists=True)

    MIGRATION.grant_portal_runtime_coordination_execute(None, schema_editor)

    schema_editor.execute.assert_not_called()
    cursor.execute.assert_not_called()
