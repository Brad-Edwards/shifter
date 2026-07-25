"""Rehome APIKey from risk_register to shared, and retire risk_register (#1374 Part B).

Sequenced strictly after ``Risk``/``Comment`` are gone (see the issue's Part
A/B plan-revision comment): ``Comment.author_apikey`` was a real Django
``ForeignKey`` to ``APIKey``. Moving ``APIKey`` into ``shared`` while
``Comment`` still lived in ``risk_register`` would have created a
cross-app-label FK, which ``management.management.commands.check_model_fks``
rejects at zero tolerance. By the time this migration exists, ``Risk`` and
``Comment`` (and the rest of the ``risk_register`` app) are deleted outright,
so there is no FK left to violate.

Three things happen here, in one migration because all three only make sense
once ``risk_register`` is actually gone:

1. **Adopt-or-create ``APIKey``**, mirroring the exact cross-vendor
   introspection pattern ``0006_rehome_auditlog.py`` used for ``AuditLog``:
   rename ``risk_register_apikey`` -> ``shared_apikey`` if the old table is
   present (upgraded deployment; rows preserved), else create the table fresh
   from model state (fresh install, or a deployment already past this
   migration in an earlier attempt).
2. **Drop the orphaned risk tables** (``risk_register_risk``,
   ``risk_register_comment``): pilot Risk Register product data, not audit
   data or archival key metadata, and there is no in-tree model left to own
   them. ``state_operations=[]`` for this step: Django's migration state
   already has no knowledge of ``Risk``/``Comment`` by this point (the whole
   ``risk_register`` app and its migrations are deleted in this same change),
   so there is no ORM state left to remove.
3. **Clean the ``django_migrations`` ledger** for the removed ``risk_register``
   app, so an upgraded database does not carry a ghost app's history forever.

All three steps are guarded to be no-ops on a fresh database (``IF EXISTS`` /
``DELETE ... WHERE`` naturally affect zero rows when there is nothing to do),
and idempotent if re-run.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models

OLD_APIKEY_TABLE = "risk_register_apikey"
NEW_APIKEY_TABLE = "shared_apikey"

# Dropped outright: pilot Risk Register product data, not audit/archival data.
_ORPHANED_RISK_TABLES = ("risk_register_comment", "risk_register_risk")

_RETIRED_APP_LABEL = "risk_register"


def _table_exists(schema_editor, table_name: str) -> bool:
    """Return True when ``table_name`` exists in the database, any vendor."""
    with schema_editor.connection.cursor() as cursor:
        return table_name in schema_editor.connection.introspection.table_names(cursor)


def adopt_or_create_apikey(apps, schema_editor) -> None:
    """Rename the old risk_register APIKey table if present; else create fresh."""
    if _table_exists(schema_editor, OLD_APIKEY_TABLE):
        if not _table_exists(schema_editor, NEW_APIKEY_TABLE):
            schema_editor.execute(f"ALTER TABLE {OLD_APIKEY_TABLE} RENAME TO {NEW_APIKEY_TABLE}")
    elif not _table_exists(schema_editor, NEW_APIKEY_TABLE):
        APIKey = apps.get_model("shared", "APIKey")
        schema_editor.create_model(APIKey)


def reverse_adopt_or_create_apikey(apps, schema_editor) -> None:
    """Rename ``shared_apikey`` back to the risk_register name if present."""
    if _table_exists(schema_editor, NEW_APIKEY_TABLE) and not _table_exists(schema_editor, OLD_APIKEY_TABLE):
        schema_editor.execute(f"ALTER TABLE {NEW_APIKEY_TABLE} RENAME TO {OLD_APIKEY_TABLE}")


def _drop_table_if_exists(schema_editor, table_name: str) -> None:
    """Drop ``table_name`` if present; CASCADE only where the vendor supports it.

    SQLite's ``DROP TABLE`` grammar has no ``CASCADE`` clause (and does not
    enforce FK constraints by default), so the clause is added only on
    Postgres. Tables are dropped in dependent-first order (comment before
    risk) regardless, so ``CASCADE`` is defense in depth, not load-bearing.
    """
    cascade = " CASCADE" if schema_editor.connection.vendor == "postgresql" else ""
    schema_editor.execute(f"DROP TABLE IF EXISTS {table_name}{cascade}")


def drop_orphaned_risk_tables(apps, schema_editor) -> None:
    """Drop the risk/comment tables outright; a no-op if already gone."""
    for table_name in _ORPHANED_RISK_TABLES:
        _drop_table_if_exists(schema_editor, table_name)


def clean_retired_app_migration_ledger(apps, schema_editor) -> None:
    """Delete ``django_migrations`` rows for the retired ``risk_register`` app.

    A plain parameterized ``DELETE ... WHERE app = %s`` against Django's own
    migration-recorder table; affects zero rows (a no-op) on a database that
    never ran risk_register's migrations.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DELETE FROM django_migrations WHERE app = %s", [_RETIRED_APP_LABEL])


class Migration(migrations.Migration):
    # No dependency on risk_register: that app and its migrations are deleted
    # in this same change (see module docstring and 0006_rehome_auditlog.py).
    dependencies = [
        ("shared", "0006_rehome_auditlog"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="APIKey",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("name", models.CharField(help_text="Human-friendly name for this key", max_length=100)),
                        (
                            "prefix",
                            models.CharField(help_text="Key prefix for identification", max_length=8, unique=True),
                        ),
                        ("key_hash", models.CharField(help_text="SHA-256 hash of full key", max_length=64)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("last_used_at", models.DateTimeField(blank=True, null=True)),
                        ("expires_at", models.DateTimeField(blank=True, null=True)),
                        ("revoked_at", models.DateTimeField(blank=True, null=True)),
                        (
                            "created_by",
                            models.ForeignKey(
                                null=True,
                                on_delete=models.deletion.SET_NULL,
                                related_name="created_api_keys",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "API Key",
                        "verbose_name_plural": "API Keys",
                        "db_table": "shared_apikey",
                        "ordering": ["-created_at"],
                        "indexes": [
                            models.Index(fields=["prefix"], name="shared_apik_prefix_1fa224_idx"),
                            models.Index(fields=["created_by", "revoked_at"], name="shared_apik_created_b4f641_idx"),
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        # A *separate* top-level operation, not folded into the block above.
        # ``SeparateDatabaseAndState.database_forwards`` recomputes its state
        # progression purely from its own ``database_operations``, so a
        # ``RunPython`` sharing a ``SeparateDatabaseAndState`` with the
        # ``CreateModel`` above would still run against a state where
        # ``shared.APIKey`` does not exist yet, and ``apps.get_model("shared",
        # "APIKey")`` in the fresh-install branch would raise ``LookupError``
        # (see the matching fix and comment in ``0006_rehome_auditlog.py``).
        # Splitting into two top-level operations lets Django's outer
        # ``Migration.apply()`` state bookkeeping carry the ``CreateModel``
        # forward before this ``RunPython`` runs.
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(adopt_or_create_apikey, reverse_adopt_or_create_apikey),
            ],
        ),
        # Risk/comment tables and the retired app's migration ledger: no Django
        # model state exists for these anymore (risk_register and its
        # migrations are deleted in this same change), so state_operations=[].
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(drop_orphaned_risk_tables, migrations.RunPython.noop),
                migrations.RunPython(clean_retired_app_migration_ledger, migrations.RunPython.noop),
            ],
        ),
    ]
