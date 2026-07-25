"""Tests for the ``shared.AuditLog`` rehome migration's adopt-or-create path (#1374).

Drives ``shared/migrations/0006_rehome_auditlog.py``'s database-operation
function directly against the real (test) database connection, in both
physical states it must handle:

- the old ``risk_register_auditlog`` table present (this Part A window, and
  any upgraded deployment) -> adopted via rename, rows preserved;
- neither table present (a fresh install once risk_register has left
  ``INSTALLED_APPS`` in a later part) -> created fresh from model state.

Each test mutates the live test-database table and relies on the surrounding
``@pytest.mark.django_db`` transaction rollback to restore it afterward, so
other tests never see the intermediate state.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from shared.models import AuditLog

pytestmark = pytest.mark.django_db

_MIGRATION = importlib.import_module("shared.migrations.0006_rehome_auditlog")


def _existing_tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


# Every non-pk, non-auto-timestamp field on ``AuditLog`` — including the
# evidentiary ones (JSON state, context, request-tracing fields) a
# single-column spot check would miss if the rename silently dropped or
# truncated them.
_AUDITLOG_FIELDS = [
    "entity_type",
    "entity_id",
    "action",
    "actor_type",
    "actor_id",
    "previous_state",
    "new_state",
    "context",
    "source_ip",
    "user_agent",
    "request_id",
]


def _snapshot(row: AuditLog) -> dict[str, object]:
    """Capture every evidentiary field on ``row`` for pre/post comparison."""
    return {field: getattr(row, field) for field in _AUDITLOG_FIELDS}


def _direct_schema_editor():
    """Build a schema_editor for direct DDL calls inside a wrapped test transaction.

    Skips the ``with schema_editor:`` lifecycle (``__enter__``/``__exit__``),
    which on SQLite toggles FK-constraint checking — unsupported mid-transaction,
    and pytest-django wraps every ``@pytest.mark.django_db`` test in one. Real
    ``migrate`` runs always apply migrations through the full context manager
    outside any such wrapping transaction, so only this direct-call test
    harness needs to skip it. ``create_model()`` still needs ``deferred_sql``
    initialized, since it defers index/constraint SQL there (normally flushed
    by ``__exit__``); the caller flushes it explicitly with ``_flush_deferred``.
    """
    schema_editor = connection.schema_editor()
    schema_editor.deferred_sql = []
    return schema_editor


def _flush_deferred(schema_editor) -> None:
    for sql in schema_editor.deferred_sql:
        schema_editor.execute(sql, None)
    schema_editor.deferred_sql = []


def test_fresh_install_creates_table_when_neither_table_exists():
    """Simulates risk_register having left INSTALLED_APPS entirely.

    No ``risk_register_auditlog`` table was ever created in that world, so the
    migration must create ``shared_auditlog`` fresh from model state rather
    than attempt a rename of a table that doesn't exist.
    """
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE shared_auditlog")
    assert "shared_auditlog" not in _existing_tables()

    schema_editor = _direct_schema_editor()
    _MIGRATION.adopt_or_create_auditlog(global_apps, schema_editor)
    _flush_deferred(schema_editor)

    assert "shared_auditlog" in _existing_tables()
    # The freshly created table is fully usable through the ORM.
    row = AuditLog.objects.create(entity_type="range", entity_id=1, action="create", actor_type="system")
    assert AuditLog.objects.get(pk=row.pk).entity_type == "range"


def test_adopt_renames_existing_risk_register_table_preserving_rows():
    """The old table, when present, is renamed in place — every column survives."""
    seeded = AuditLog.objects.create(
        entity_type="range",
        entity_id=42,
        action="create",
        actor_type="system",
        actor_id=99,
        previous_state={"status": "pending", "count": 1},
        new_state={"status": "ready", "count": 2},
        context="seeded for full-row migration preservation check",
        source_ip="203.0.113.7",
        user_agent="pytest-migration-preservation/1.0",
        request_id="req-migration-preserve-0001",
    )
    before = _snapshot(seeded)
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE shared_auditlog RENAME TO risk_register_auditlog")
    assert _existing_tables() >= {"risk_register_auditlog"}
    assert "shared_auditlog" not in _existing_tables()

    schema_editor = connection.schema_editor()
    _MIGRATION.adopt_or_create_auditlog(global_apps, schema_editor)

    tables = _existing_tables()
    assert "shared_auditlog" in tables
    assert "risk_register_auditlog" not in tables
    # Every field on the row that existed before the rename — not just one
    # spot-checked column — is still intact afterward.
    after = _snapshot(AuditLog.objects.get(pk=seeded.pk))
    assert after == before


def test_adopt_or_create_is_idempotent_when_shared_table_already_present():
    """Calling forward again when ``shared_auditlog`` already exists is a no-op."""
    schema_editor = connection.schema_editor()
    _MIGRATION.adopt_or_create_auditlog(global_apps, schema_editor)  # already exists; must not raise
    assert "shared_auditlog" in _existing_tables()


def test_reverse_renames_shared_table_back_to_risk_register_name():
    """The reverse migration mirrors the forward rename."""
    schema_editor = connection.schema_editor()
    _MIGRATION.reverse_adopt_or_create_auditlog(global_apps, schema_editor)

    tables = _existing_tables()
    assert "risk_register_auditlog" in tables
    assert "shared_auditlog" not in tables


class TestForwardMigrationStateSequencing:
    """Regression test for the state-sequencing contract this migration's module
    docstring describes.

    The migration is split into two top-level ``SeparateDatabaseAndState``
    operations specifically because ``SeparateDatabaseAndState.database_forwards``
    recomputes each operation's ``from_state`` purely from that *same*
    operation's own ``database_operations`` — never from a sibling
    ``state_operations`` list. Calling ``adopt_or_create_auditlog`` directly
    with ``global_apps`` (as every test above does) always finds ``AuditLog``
    registered, because the live app registry has every model regardless of
    migration history — so that style of test cannot catch a regression where
    the two blocks are merged back together.

    This test instead drives the real migration file through Django's
    ``MigrationExecutor`` against a genuinely frozen historical
    ``ProjectState``, built by rewinding the database to the migration
    immediately prior and re-advancing it. Confirmed locally (then reverted)
    that merging the two blocks back into one reproduces exactly the
    ``LookupError`` the module docstring describes, raised from this test.
    """

    pytestmark = pytest.mark.django_db(transaction=True)

    _MIGRATE_FROM = [("shared", "0005_alter_acesparticipantruntimerecord_record_kind")]
    _MIGRATE_TO = [("shared", "0006_rehome_auditlog")]
    _MIGRATE_HEAD = [("shared", "0007_rehome_apikey_and_retire_risk_register")]

    def test_adopt_or_create_runs_against_frozen_prior_state_via_real_executor(self):
        """Forward-migrating through 0006 alone must succeed from a state where
        ``AuditLog`` was never in the registry until this migration's own
        ``CreateModel`` runs — the exact scenario ``global_apps`` cannot represent.
        """
        executor = MigrationExecutor(connection)
        try:
            # Rewind "shared" to just before this migration. Nothing else in
            # the project depends on shared's 0006/0007 (no other app's
            # migrations name them), so this only unapplies those two,
            # reverse-renaming shared_auditlog/shared_apikey back to their
            # risk_register names along the way.
            executor.migrate(self._MIGRATE_FROM)

            # Force the true fresh-install branch this migration's `elif`
            # handles: drop whichever table the rewind left behind, so
            # neither name exists — the one path where the split-block
            # sequencing matters (`apps.get_model(...)` inside the RunPython
            # target only runs in this branch).
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS risk_register_auditlog")
                cursor.execute("DROP TABLE IF EXISTS shared_auditlog")

            # Reload: the loader must see the DDL above and the updated
            # django_migrations ledger the rewind left behind.
            executor = MigrationExecutor(connection)
            executor.migrate(self._MIGRATE_TO)

            assert "shared_auditlog" in _existing_tables()
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(self._MIGRATE_HEAD)
