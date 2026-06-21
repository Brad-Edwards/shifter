"""Generate the committed settings env-var manifest (#948)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from config._env_manifest import write_manifest


class Command(BaseCommand):
    help = "Regenerate config/env-manifest.json from config settings modules."

    def handle(self, *args, **options):
        path = write_manifest()
        count = len(path.read_text().split('"name"')) - 1
        self.stdout.write(self.style.SUCCESS(f"Wrote {path} ({count} variables)"))
