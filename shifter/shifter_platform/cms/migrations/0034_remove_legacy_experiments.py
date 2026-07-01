from django.db import migrations


EXPERIMENT_TABLES = (
    "experiments_runartifact",
    "experiments_experimentartifact",
    "experiments_experimentscript",
    "experiments_experimentrun",
    "experiments_experiment",
    "experiments_scriptasset",
)


def remove_legacy_experiments(_apps, schema_editor):
    suffix = " CASCADE" if schema_editor.connection.vendor == "postgresql" else ""
    with schema_editor.connection.cursor() as cursor:
        for table in EXPERIMENT_TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(table)}{suffix}")
        cursor.execute("DELETE FROM django_content_type WHERE app_label = %s", ["experiments"])
        cursor.execute("DELETE FROM django_migrations WHERE app = %s", ["experiments"])


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0033_backfill_ctf_range_source"),
    ]

    operations = [
        migrations.RunPython(remove_legacy_experiments, migrations.RunPython.noop),
    ]
