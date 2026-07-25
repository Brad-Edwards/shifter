"""Rehome AuditLog from risk_register to shared (#1374).

``AuditLog`` moves to the ``shared`` app so the durable audit store is owned by
a Django app that survives the Risk Register feature removal (ADR-001). The
table is renamed from ``risk_register_auditlog`` to ``shared_auditlog``,
preserving every row — this is an adopt, never a drop/recreate.

Two physical database states are possible when this migration runs:

- An upgraded deployment where ``risk_register`` has already created
  ``risk_register_auditlog`` (via its own historical migrations): the table is
  renamed in place, so historical rows survive untouched.
- A fresh install after ``risk_register`` has left ``INSTALLED_APPS``
  (Part B/C): no ``risk_register_auditlog`` table was ever created, so the
  table is created fresh from the ``shared`` model state.

Both branches are guarded on the schema-editor's own cross-database table
introspection (not a Postgres-only catalog function, so this also runs
correctly on the SQLite test lane) and are idempotent; the reverse mirrors the
forward operation.

Part A and Part B of #1374 ship in the same change (there is no released
intermediate state where risk_register is gone from ``INSTALLED_APPS`` but this
migration predates that removal), so this migration has no dependency on any
``risk_register`` migration: by the time it runs, ``risk_register`` and its
entire ``migrations/`` package no longer exist in the tree (Part B), and a
dependency naming one of its migrations would make the graph itself fail to
build (``NodeNotFoundError``) once that app is gone. The adopt-vs-create branch
above is chosen from live database introspection, not migration-graph
ordering, so no such dependency was ever load-bearing for correctness.
"""

from __future__ import annotations

from django.db import migrations, models

OLD_TABLE = "risk_register_auditlog"
NEW_TABLE = "shared_auditlog"


def _table_exists(schema_editor, table_name: str) -> bool:
    """Return True when ``table_name`` exists in the database, any vendor."""
    with schema_editor.connection.cursor() as cursor:
        return table_name in schema_editor.connection.introspection.table_names(cursor)


def adopt_or_create_auditlog(apps, schema_editor) -> None:
    """Rename the old table if present; otherwise create the new one fresh."""
    if _table_exists(schema_editor, OLD_TABLE):
        if not _table_exists(schema_editor, NEW_TABLE):
            schema_editor.execute(f"ALTER TABLE {OLD_TABLE} RENAME TO {NEW_TABLE}")
    elif not _table_exists(schema_editor, NEW_TABLE):
        AuditLog = apps.get_model("shared", "AuditLog")
        schema_editor.create_model(AuditLog)


def reverse_adopt_or_create_auditlog(apps, schema_editor) -> None:
    """Rename ``shared_auditlog`` back to the risk_register name if present."""
    if _table_exists(schema_editor, NEW_TABLE) and not _table_exists(schema_editor, OLD_TABLE):
        schema_editor.execute(f"ALTER TABLE {NEW_TABLE} RENAME TO {OLD_TABLE}")


class Migration(migrations.Migration):
    # No dependency on risk_register (see module docstring): that app and its
    # migrations are removed in the same change, so naming one of its
    # migrations here would leave the graph referencing a node that no longer
    # exists once risk_register is gone (#1374 Part B).
    dependencies = [
        ("shared", "0005_alter_acesparticipantruntimerecord_record_kind"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="AuditLog",
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
                        (
                            "entity_type",
                            models.CharField(
                                choices=[
                                    ("apikey", "API Key"),
                                    ("range", "Range"),
                                    ("credential", "Credential"),
                                    ("agent", "Agent"),
                                    ("user", "User"),
                                    ("session", "Session"),
                                    ("ngfw", "NGFW"),
                                    ("config", "Configuration"),
                                    ("experiment", "Experiment"),
                                    ("scenario", "Scenario"),
                                    ("script", "Script"),
                                ],
                                max_length=20,
                            ),
                        ),
                        ("entity_id", models.PositiveIntegerField()),
                        (
                            "action",
                            models.CharField(
                                choices=[
                                    ("create", "Create"),
                                    ("update", "Update"),
                                    ("delete", "Delete"),
                                    ("restore", "Restore"),
                                    ("close", "Close"),
                                    ("reopen", "Reopen"),
                                    ("login", "Login"),
                                    ("logout", "Logout"),
                                    ("login_failed", "Login Failed"),
                                    ("access_denied", "Access Denied"),
                                    ("role_sync", "Role Sync"),
                                    ("connect", "Connect"),
                                    ("disconnect", "Disconnect"),
                                    ("download", "Download"),
                                    ("provision", "Provision"),
                                    ("deprovision", "Deprovision"),
                                    ("ready", "Ready"),
                                    ("failed", "Failed"),
                                    ("pause", "Pause"),
                                    ("resume", "Resume"),
                                    ("cancel", "Cancel"),
                                    ("recover", "Recover"),
                                    ("spare_provision", "Spare Provision"),
                                ],
                                max_length=20,
                            ),
                        ),
                        (
                            "actor_type",
                            models.CharField(
                                choices=[
                                    ("user", "User"),
                                    ("apikey", "API Key"),
                                    ("system", "System"),
                                    ("cognito", "Cognito"),
                                ],
                                max_length=10,
                            ),
                        ),
                        ("actor_id", models.PositiveIntegerField(blank=True, null=True)),
                        ("timestamp", models.DateTimeField(auto_now_add=True)),
                        ("previous_state", models.JSONField(blank=True, null=True)),
                        ("new_state", models.JSONField(blank=True, null=True)),
                        ("context", models.TextField(blank=True, help_text="Optional reason or notes")),
                        ("source_ip", models.GenericIPAddressField(blank=True, null=True)),
                        ("user_agent", models.CharField(blank=True, max_length=500)),
                        ("request_id", models.CharField(blank=True, db_index=True, max_length=64)),
                    ],
                    options={
                        "verbose_name": "Audit Log",
                        "verbose_name_plural": "Audit Logs",
                        "db_table": "shared_auditlog",
                        "ordering": ["-timestamp"],
                        "indexes": [
                            models.Index(fields=["entity_type", "entity_id"], name="shared_audi_entity__48f0b9_idx"),
                            models.Index(fields=["actor_type", "actor_id"], name="shared_audi_actor_t_e3f686_idx"),
                            models.Index(fields=["timestamp"], name="shared_audi_timesta_860574_idx"),
                            models.Index(fields=["action"], name="shared_audi_action_a9dbd0_idx"),
                        ],
                    },
                ),
            ],
            database_operations=[],
        ),
        # A *separate* top-level operation, not folded into the block above.
        # ``SeparateDatabaseAndState.database_forwards`` recomputes its state
        # progression purely from its own ``database_operations`` (see its
        # implementation), so a ``RunPython`` sharing a ``SeparateDatabaseAndState``
        # with the ``CreateModel`` above would still run against a state where
        # ``shared.AuditLog`` does not exist yet, and ``apps.get_model("shared",
        # "AuditLog")`` in the fresh-install branch would raise ``LookupError``.
        # Splitting into two top-level operations lets Django's outer
        # ``Migration.apply()`` state bookkeeping carry the ``CreateModel``
        # forward before this ``RunPython`` runs.
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(adopt_or_create_auditlog, reverse_adopt_or_create_auditlog),
            ],
        ),
    ]
