"""Tests for the #1516 data migration that revokes self-service-derived organizers.

The repo has no dedicated migration-test harness, so the migration's forward
function is imported directly and driven against seeded data with the real app
registry. It proves the fail-closed revocation (per maintainer decision
2026-07-11): every current ``CTF Organizer`` member loses the group, each removal
is audited, and non-organizer memberships are untouched.

Since #1374 the migration writes its audit row via a raw, parameterized
``INSERT`` (not the ORM) so it has no migration dependency on the audit
rehome — see the migration's module docstring. Driving it directly therefore
requires a real ``schema_editor`` (not ``None``): ``django.db.connection`` is
already resolved to whichever table the audit rehome has adopted in this test
run's database.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection

from shared.audit import AuditAction
from shared.auth import CTF_ORGANIZER_GROUP, CTF_PARTICIPANT_GROUP
from shared.models import AuditLog

User = get_user_model()

_MIGRATION = importlib.import_module("management.migrations.0008_revoke_self_service_organizers")


def _run_migration_forward():
    # Built directly, not via the ``with connection.schema_editor()`` context
    # manager: entering that context toggles SQLite FK-constraint checking,
    # which SQLite refuses mid-transaction — and pytest-django wraps every
    # ``@pytest.mark.django_db`` test in one. Real ``migrate`` runs apply this
    # migration through the full context manager outside any such wrapping
    # transaction; only this direct-call test harness needs to skip it. The
    # migration itself only calls ``.execute()``, which needs no DDL lifecycle.
    schema_editor = connection.schema_editor()
    _MIGRATION.revoke_self_service_organizers(global_apps, schema_editor)


@pytest.mark.django_db
def test_migration_revokes_and_audits_existing_organizers():
    organizer = Group.objects.get_or_create(name=CTF_ORGANIZER_GROUP)[0]
    participant = Group.objects.get_or_create(name=CTF_PARTICIPANT_GROUP)[0]

    org_user = User.objects.create_user(username="org@example.com", email="org@example.com")
    org_user.groups.add(organizer, participant)
    plain_participant = User.objects.create_user(username="p@example.com", email="p@example.com")
    plain_participant.groups.add(participant)

    _run_migration_forward()

    org_user.refresh_from_db()
    plain_participant.refresh_from_db()
    # Organizer removed; the user's other (participant) membership is untouched.
    assert set(org_user.groups.values_list("name", flat=True)) == {CTF_PARTICIPANT_GROUP}
    # A non-organizer is unaffected and gets no audit row.
    assert set(plain_participant.groups.values_list("name", flat=True)) == {CTF_PARTICIPANT_GROUP}

    # The migration's own revocation audit row, isolated by its context (the
    # organizer-authority m2m signal also audits real-model group changes in this
    # test, so filter to the migration's row rather than asserting a total count).
    migration_rows = AuditLog.objects.filter(
        entity_type="user",
        entity_id=org_user.id,
        action=AuditAction.ROLE_SYNC,
        context__icontains="separated from self-service",
    )
    assert migration_rows.count() == 1
    row = migration_rows.get()
    assert CTF_ORGANIZER_GROUP in row.previous_state["groups"]
    assert CTF_ORGANIZER_GROUP not in row.new_state["groups"]
    # A non-organizer is unaffected: no organizer change, so no audit row at all.
    assert AuditLog.objects.filter(entity_id=plain_participant.id).count() == 0


@pytest.mark.django_db
def test_migration_no_op_when_no_organizer_members():
    Group.objects.get_or_create(name=CTF_ORGANIZER_GROUP)
    _run_migration_forward()
    assert AuditLog.objects.filter(action=AuditAction.ROLE_SYNC).count() == 0


@pytest.mark.django_db
def test_writes_row_against_legacy_table_name_when_rehome_not_applied():
    """Lagging-upgrade path: only the pre-#1374 ``risk_register_auditlog`` table exists.

    Simulates a database that has not yet run the audit rehome migration by
    temporarily renaming the live table back to its old name, then drives the
    migration's own table-resolution and row-write helpers directly (not the
    full ``revoke_self_service_organizers`` orchestration, which also mutates
    ``user.groups`` and would trip the unrelated organizer-authority m2m
    signal's own ORM-mapped audit write — that signal isn't in scope for this
    fallback and only exists because this test un-renames the table
    mid-transaction, a state real deployments never hit outside a migration).
    Verified via a raw read-back rather than the ORM, since the ORM's
    ``shared.models.AuditLog`` is mapped to the *new* table name and can't see
    a row filed under the old one.
    """
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE shared_auditlog RENAME TO risk_register_auditlog")
    try:
        schema_editor = connection.schema_editor()
        table_name = _MIGRATION._resolve_audit_table(schema_editor)
        assert table_name == "risk_register_auditlog"

        _MIGRATION._write_role_sync_row(
            schema_editor,
            table_name,
            user_id=999999,
            previous={"groups": [CTF_ORGANIZER_GROUP]},
            new={"groups": []},
            context="CTF Organizer revoked: authority separated from self-service identity (issue #1516)",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT action, context FROM risk_register_auditlog WHERE entity_id = %s AND entity_type = 'user'",
                [999999],
            )
            row = cursor.fetchone()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE risk_register_auditlog RENAME TO shared_auditlog")

    assert row is not None
    assert row[0] == AuditAction.ROLE_SYNC
    assert "separated from self-service" in row[1]


def test_resolve_audit_table_raises_when_neither_table_exists(monkeypatch):
    """Defensive fail-loud path: an unreachable state (neither table exists)."""

    class _EmptyIntrospection:
        @staticmethod
        def table_names(cursor):
            return []

    class _FakeConnection:
        introspection = _EmptyIntrospection()

        def cursor(self):
            import contextlib

            return contextlib.nullcontext(None)

    class _FakeSchemaEditor:
        connection = _FakeConnection()

    schema_editor = _FakeSchemaEditor()

    with pytest.raises(RuntimeError, match="Neither shared_auditlog nor risk_register_auditlog"):
        _MIGRATION._resolve_audit_table(schema_editor)
