"""Tests for the ``shared.APIKey`` rehome migration (#1374 Part B).

Drives ``shared/migrations/0007_rehome_apikey_and_retire_risk_register.py``'s
database-operation functions directly against the real (test) database
connection. Mirrors the pattern established for the ``AuditLog`` rehome in
``test_migrations_rehome_auditlog.py``, plus coverage for the two operations
that only make sense once ``risk_register`` is gone: dropping the orphaned
``Risk``/``Comment`` tables and cleaning the retired app's
``django_migrations`` ledger rows.

Each test mutates the live test-database table and relies on the surrounding
``@pytest.mark.django_db`` transaction rollback to restore it afterward, so
other tests never see the intermediate state.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as global_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from shared.models import APIKey

pytestmark = pytest.mark.django_db

_MIGRATION = importlib.import_module("shared.migrations.0007_rehome_apikey_and_retire_risk_register")


def _existing_tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


# Every non-pk, non-auto-timestamp field on ``APIKey`` — including
# ``key_hash``/``prefix``, the fields with actual security value a
# single-column spot check would miss if the rename silently dropped or
# truncated them.
_APIKEY_FIELDS = [
    "name",
    "prefix",
    "key_hash",
    "created_by_id",
    "last_used_at",
    "expires_at",
    "revoked_at",
]


def _snapshot(row: APIKey) -> dict[str, object]:
    """Capture every security-relevant field on ``row`` for pre/post comparison."""
    return {field: getattr(row, field) for field in _APIKEY_FIELDS}


def _direct_schema_editor():
    """Build a schema_editor for direct DDL calls inside a wrapped test transaction.

    See the identical helper (and its rationale) in
    ``test_migrations_rehome_auditlog.py``.
    """
    schema_editor = connection.schema_editor()
    schema_editor.deferred_sql = []
    return schema_editor


def _flush_deferred(schema_editor) -> None:
    for sql in schema_editor.deferred_sql:
        schema_editor.execute(sql, None)
    schema_editor.deferred_sql = []


class TestAdoptOrCreateApiKey:
    def test_fresh_install_creates_table_when_neither_table_exists(self):
        """Simulates a fresh install: no risk_register_apikey table ever existed."""
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE shared_apikey")
        assert "shared_apikey" not in _existing_tables()

        schema_editor = _direct_schema_editor()
        _MIGRATION.adopt_or_create_apikey(global_apps, schema_editor)
        _flush_deferred(schema_editor)

        assert "shared_apikey" in _existing_tables()
        row = APIKey.objects.create(name="fresh", prefix="frsh0001", key_hash="x" * 64)
        assert APIKey.objects.get(pk=row.pk).name == "fresh"

    def test_adopt_renames_existing_risk_register_table_preserving_rows(self, django_user_model):
        """The old table, when present, is renamed in place — every column survives."""
        owner = django_user_model.objects.create_user(username="legacy-key-owner", password="pw")
        seeded = APIKey.objects.create(
            name="legacy migration key",
            prefix="lgcy0001",
            key_hash="b1ake2s-legacy-key-hash-fixture-0123456789abcdef0123456789abcd",
            created_by=owner,
            last_used_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=30),
            revoked_at=timezone.now() - timedelta(days=2),
        )
        before = _snapshot(seeded)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE shared_apikey RENAME TO risk_register_apikey")
        assert _existing_tables() >= {"risk_register_apikey"}
        assert "shared_apikey" not in _existing_tables()

        schema_editor = connection.schema_editor()
        _MIGRATION.adopt_or_create_apikey(global_apps, schema_editor)

        tables = _existing_tables()
        assert "shared_apikey" in tables
        assert "risk_register_apikey" not in tables
        # Every field on the row that existed before the rename — not just
        # ``name`` — is still intact afterward, including ``key_hash``.
        after = _snapshot(APIKey.objects.get(pk=seeded.pk))
        assert after == before

    def test_adopt_or_create_is_idempotent_when_shared_table_already_present(self):
        schema_editor = connection.schema_editor()
        _MIGRATION.adopt_or_create_apikey(global_apps, schema_editor)  # already exists; must not raise
        assert "shared_apikey" in _existing_tables()

    def test_reverse_renames_shared_table_back_to_risk_register_name(self):
        schema_editor = connection.schema_editor()
        _MIGRATION.reverse_adopt_or_create_apikey(global_apps, schema_editor)

        tables = _existing_tables()
        assert "risk_register_apikey" in tables
        assert "shared_apikey" not in tables


class TestDropOrphanedRiskTables:
    def test_drops_risk_and_comment_tables_when_present(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE risk_register_risk (id integer primary key)")
            cursor.execute("CREATE TABLE risk_register_comment (id integer primary key)")
        assert _existing_tables() >= {"risk_register_risk", "risk_register_comment"}

        schema_editor = connection.schema_editor()
        _MIGRATION.drop_orphaned_risk_tables(global_apps, schema_editor)

        tables = _existing_tables()
        assert "risk_register_risk" not in tables
        assert "risk_register_comment" not in tables

    def test_is_a_no_op_when_neither_table_exists(self):
        """A fresh database never had these tables; dropping is a safe no-op."""
        assert "risk_register_risk" not in _existing_tables()
        assert "risk_register_comment" not in _existing_tables()

        schema_editor = connection.schema_editor()
        _MIGRATION.drop_orphaned_risk_tables(global_apps, schema_editor)  # must not raise

        tables = _existing_tables()
        assert "risk_register_risk" not in tables
        assert "risk_register_comment" not in tables


class TestCleanRetiredAppMigrationLedger:
    def test_deletes_rows_for_the_retired_app(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
                ["risk_register", "0001_initial", "2020-01-01 00:00:00"],
            )
            cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app = %s", ["risk_register"])
            assert cursor.fetchone()[0] == 1

        schema_editor = connection.schema_editor()
        _MIGRATION.clean_retired_app_migration_ledger(global_apps, schema_editor)

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app = %s", ["risk_register"])
            assert cursor.fetchone()[0] == 0

    def test_is_a_no_op_when_no_rows_exist(self):
        """A fresh database never recorded risk_register migrations; a safe no-op."""
        schema_editor = connection.schema_editor()
        _MIGRATION.clean_retired_app_migration_ledger(global_apps, schema_editor)  # must not raise

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app = %s", ["risk_register"])
            assert cursor.fetchone()[0] == 0


class TestForwardMigrationStateSequencing:
    """Regression test for the state-sequencing contract this migration's module
    docstring describes.

    Mirrors ``TestForwardMigrationStateSequencing`` in
    ``test_migrations_rehome_auditlog.py`` — see that class's docstring for
    the full mechanism. Calling ``adopt_or_create_apikey`` directly with
    ``global_apps`` (as every test above does) always finds ``APIKey``
    registered, since the live app registry has every model regardless of
    migration history; only driving the real migration file through
    ``MigrationExecutor`` against a frozen historical ``ProjectState``
    exercises the split-block sequencing this migration's comments describe.
    Confirmed locally (then reverted) that merging the blocks reproduces the
    ``LookupError`` the module docstring warns about, raised from this test.
    """

    pytestmark = pytest.mark.django_db(transaction=True)

    _MIGRATE_FROM = [("shared", "0006_rehome_auditlog")]
    _MIGRATE_TO = [("shared", "0007_rehome_apikey_and_retire_risk_register")]

    def test_adopt_or_create_runs_against_frozen_prior_state_via_real_executor(self):
        """Forward-migrating through 0007 alone must succeed from a state where
        ``APIKey`` was never in the registry until this migration's own
        ``CreateModel`` runs — the exact scenario ``global_apps`` cannot represent.
        """
        executor = MigrationExecutor(connection)
        try:
            # Rewind "shared" to just before this migration -- nothing else in
            # the project depends on shared's 0007, so this unapplies only
            # that one migration, reverse-renaming shared_apikey back to
            # risk_register_apikey along the way.
            executor.migrate(self._MIGRATE_FROM)

            # Force the true fresh-install branch this migration's `elif`
            # handles: drop whichever table the rewind left behind, so
            # neither name exists -- the one path where the split-block
            # sequencing matters (`apps.get_model(...)` inside the RunPython
            # target only runs in this branch).
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS risk_register_apikey")
                cursor.execute("DROP TABLE IF EXISTS shared_apikey")

            # Reload: the loader must see the DDL above and the updated
            # django_migrations ledger the rewind left behind.
            executor = MigrationExecutor(connection)
            executor.migrate(self._MIGRATE_TO)

            assert "shared_apikey" in _existing_tables()
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(self._MIGRATE_TO)
