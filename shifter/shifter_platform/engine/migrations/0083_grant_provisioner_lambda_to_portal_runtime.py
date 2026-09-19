"""Grant portal_runtime membership in provisioner_lambda (GCP/GKE range path).

On the GCP/GKE range path the provisioner Job connects to Postgres as
``portal_runtime`` (the launcher forwards DB_USER/DB_PASSWORD from the runtime
config, and platform-runtime's DB_USER is portal_runtime). Every provisioner DB
capability, however, is granted to the dedicated ``provisioner_lambda`` role
(engine migrations 0006/0013/0025/0034/0036/0046, ...). Without membership,
``portal_runtime`` cannot execute e.g. ``engine_reserve_subnet_cidrs`` and range
provisioning fails with ``permission denied for function ...``.

Grant ``provisioner_lambda`` TO ``portal_runtime`` so the provisioner inherits
those capabilities. Guarded so it is a no-op where either role is absent
(idempotent; safe on every backend). The broader least-privilege question --
whether the provisioner should instead authenticate as ``provisioner_lambda``
directly rather than inherit via the broad ``portal_runtime`` -- is tracked in
Brad-Edwards/shifter#2279.
"""

from django.db import migrations

_GRANT = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'provisioner_lambda')
       AND EXISTS (SELECT FROM pg_roles WHERE rolname = 'portal_runtime') THEN
        GRANT provisioner_lambda TO portal_runtime;
    END IF;
END
$$;
"""

_REVOKE = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'provisioner_lambda')
       AND EXISTS (SELECT FROM pg_roles WHERE rolname = 'portal_runtime') THEN
        REVOKE provisioner_lambda FROM portal_runtime;
    END IF;
END
$$;
"""


class Migration(migrations.Migration):
    """Make portal_runtime a member of provisioner_lambda so the provisioner inherits its grants."""

    dependencies = [
        ("engine", "0082_model_policy_transition"),
        # portal_runtime is created by the mission_control runtime-user migration.
        ("mission_control", "0041_create_portal_runtime_user"),
    ]

    operations = [
        migrations.RunSQL(sql=_GRANT, reverse_sql=_REVOKE, elidable=False),
    ]
