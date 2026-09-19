"""Grant the GCP ``portal_runtime`` login EXECUTE on the subnet coordination routines.

On GCP the portal/provisioner connects to PostgreSQL as ``portal_runtime``
(mission_control 0041), not ``provisioner_lambda`` / ``provisioner_runtime``. The
subnet coordination functions (engine 0046, reconciled in 0083) are granted only
to those provisioner roles, so on GCP ``portal_runtime`` cannot reserve, read, or
release range subnets and range launch fails with
``permission denied for function public.engine_reserve_subnet_cidrs`` (#2219).

Grant the same narrow, function-only capability to ``portal_runtime`` when that
login exists. The functions stay SECURITY DEFINER and owned by the migration
role, so EXECUTE only lets ``portal_runtime`` call them; it confers no ability to
modify the routines or the underlying ``engine_subnetallocation`` table (which
``portal_runtime`` already reaches through its schema-wide DML from 0041). AWS
deployments have no ``portal_runtime`` login and are unaffected.
"""

from django.db import migrations

_PORTAL_RUNTIME_GRANTS = """
GRANT EXECUTE ON FUNCTION public.engine_reserve_subnet_cidrs(
    text, uuid, uuid, text, cidr, integer, integer, cidr[], text) TO portal_runtime;
GRANT EXECUTE ON FUNCTION public.engine_read_subnet_reservation(text, uuid, uuid) TO portal_runtime;
GRANT EXECUTE ON FUNCTION public.engine_release_subnet_reservation(text, uuid, uuid) TO portal_runtime;
"""


def grant_portal_runtime_coordination_execute(apps, schema_editor) -> None:
    """Grant the GCP portal_runtime login EXECUTE on the coordination functions."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ["portal_runtime"])
        portal_runtime_exists = cursor.fetchone() is not None
    if portal_runtime_exists:
        schema_editor.execute(_PORTAL_RUNTIME_GRANTS)


class Migration(migrations.Migration):
    """Run the grant only after the coordination functions and portal_runtime exist."""

    dependencies = [
        ("engine", "0084_reconcile_range_ngfw_instance_grant"),
        ("mission_control", "0044_revoke_residual_grants_from_provisioner"),
    ]

    operations = [
        # Additive, function-only capability. Reversing this ordering repair must
        # not revoke the durable privilege contract, so the reverse is a no-op.
        migrations.RunPython(grant_portal_runtime_coordination_execute, migrations.RunPython.noop),
    ]
