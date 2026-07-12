"""Register the in-box scenario catalog through the uniform ingestion path (#1578).

Loads the declared in-box pack manifest and registers each entry through the same
:func:`cms.services.register_pack` service an operator uses — the in-box catalog
has no privileged load path (ADR-033/ADR-034). Idempotent: already-registered
packs are skipped, so it is safe to run after every deploy.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError, CommandParser

from cms.exceptions import CMSError
from cms.scenarios.inbox import register_inbox_packs

User = get_user_model()


class Command(BaseCommand):
    """Bootstrap the shipped in-box catalog through the uniform ingestion service."""

    help = "Register the in-box scenario catalog through the uniform ingestion service (#1578)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare the registering-actor argument."""
        parser.add_argument("--actor", required=True, help="Username of the registering (system/admin) user.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Register the declared in-box packs, surfacing failures cleanly."""
        actor = self._resolve_actor(options["actor"])
        try:
            registered = register_inbox_packs(actor=actor)
        except (CMSError, PermissionDenied, TypeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Registered {len(registered)} in-box pack(s)."))

    def _resolve_actor(self, username: str) -> Any:
        """Return the registering user or raise a clean ``CommandError``."""
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"actor '{username}' not found") from exc
