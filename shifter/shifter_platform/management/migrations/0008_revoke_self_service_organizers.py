"""Data migration: revoke self-service-derived CTF Organizer memberships.

Issue #1516. Before this change the ``CTF Organizer`` group could be granted from
a self-mutable ``user_type`` claim, so existing memberships cannot be assumed to
be administrator-authorized. This migration removes ``CTF Organizer`` from every
current member and records a strict ROLE_SYNC audit row per removal (fail-closed,
per maintainer decision 2026-07-11). Organizer authority is re-granted only from
administrator-controlled provider group claims or explicit local assignment (see
``config.organizer_authority``): re-run the affected provider login, or a
superuser re-adds the group in the Django admin.

The reverse is intentionally a no-op — re-adding revoked memberships would
reintroduce the unverified authority this migration exists to remove.

This migration is already applied on deployed databases, so it deliberately has
no dependency on the risk-register app or the shared app (#1374): gaining a
dependency on the audit rehome migration would give an already-applied
migration an unapplied parent, and ``MigrationLoader.check_consistent_history()``
raises ``InconsistentMigrationHistory`` for that shape on every existing
deployment. Instead the audit row is written with a raw, parameterized
``INSERT`` against whichever physical audit table exists, resolved at runtime
from a fixed two-element literal set (never from input) so this keeps working
whether or not a given database has run the audit rehome migration yet.
"""

from __future__ import annotations

import json

from django.db import migrations
from django.utils import timezone

ORGANIZER_GROUP = "CTF Organizer"

# The physical audit table, resolved at runtime from this fixed, literal set —
# never from user input. "shared_auditlog" is the post-#1374 rehomed name;
# the older name is kept so a database that has not yet applied the rehome
# migration still gets its audit row.
_AUDIT_TABLE_CANDIDATES = ("shared_auditlog", "risk_register_auditlog")

# One fully literal INSERT per candidate table. SQL identifiers cannot be bind
# parameters, so the alternative is interpolating the table name into the
# statement; writing both out instead keeps this module free of dynamically
# constructed SQL entirely, which is a stronger guarantee than a justified
# suppression. Values stay parameterized.
_AUDIT_INSERT_BY_TABLE = {
    "shared_auditlog": (
        "INSERT INTO shared_auditlog "
        "(entity_type, entity_id, action, actor_type, actor_id, timestamp, "
        "previous_state, new_state, context, source_ip, user_agent, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    ),
    "risk_register_auditlog": (
        "INSERT INTO risk_register_auditlog "
        "(entity_type, entity_id, action, actor_type, actor_id, timestamp, "
        "previous_state, new_state, context, source_ip, user_agent, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    ),
}


def _resolve_audit_table(schema_editor) -> str:
    """Return whichever candidate audit table exists in the database."""
    with schema_editor.connection.cursor() as cursor:
        existing_tables = set(schema_editor.connection.introspection.table_names(cursor))
    for table_name in _AUDIT_TABLE_CANDIDATES:
        if table_name in existing_tables:
            return table_name
    raise RuntimeError(
        "Neither shared_auditlog nor risk_register_auditlog exists; cannot record "
        "the fail-closed ROLE_SYNC audit row required by issue #1516."
    )


def _write_role_sync_row(
    schema_editor,
    table_name: str,
    *,
    user_id: int,
    previous: dict,
    new: dict,
    context: str,
) -> None:
    """Insert one ROLE_SYNC audit row via a parameterized raw INSERT.

    ``table_name`` selects a fully literal statement from
    ``_AUDIT_INSERT_BY_TABLE``; no SQL is built by string construction, and the
    row values are bind parameters.
    """
    schema_editor.execute(
        _AUDIT_INSERT_BY_TABLE[table_name],
        [
            "user",
            user_id,
            "role_sync",
            "system",
            None,
            timezone.now(),
            json.dumps(previous),
            json.dumps(new),
            context,
            None,
            "",
            "",
        ],
    )


def revoke_self_service_organizers(apps, schema_editor):
    """Remove CTF Organizer from all current members, auditing each removal."""
    Group = apps.get_model("auth", "Group")

    try:
        organizer_group = Group.objects.get(name=ORGANIZER_GROUP)
    except Group.DoesNotExist:
        return

    members = list(organizer_group.user_set.all())
    if not members:
        return

    audit_table = _resolve_audit_table(schema_editor)
    for user in members:
        previous = sorted(user.groups.values_list("name", flat=True))
        user.groups.remove(organizer_group)
        new = sorted(user.groups.values_list("name", flat=True))
        _write_role_sync_row(
            schema_editor,
            audit_table,
            user_id=user.id,
            previous={"groups": previous},
            new={"groups": new},
            context="CTF Organizer revoked: authority separated from self-service identity (issue #1516)",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("management", "0007_userprofile_organizer_grant_source"),
    ]

    operations = [
        migrations.RunPython(revoke_self_service_organizers, migrations.RunPython.noop),
    ]
