"""Controlled maintenance cutover; prints counts and bounded codes only."""

from django.core.management.base import BaseCommand, CommandError

from ctf.exceptions import CTFCommunicationError
from ctf.services.communication.cutover import activate_cutover, migrate_legacy_batch, start_cutover


class Command(BaseCommand):
    help = "Fence legacy notification writers and migrate one bounded batch; repeat to resume."

    def add_arguments(self, parser):
        parser.add_argument("--legacy-producers-stopped", action="store_true")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--activate", action="store_true")

    def handle(self, *args, **options):
        try:
            start_cutover(legacy_producers_stopped=options["legacy_producers_stopped"])
            migrated = migrate_legacy_batch(batch_size=options["batch_size"])
            self.stdout.write(f"mapped={migrated}")
            if options["activate"]:
                result = activate_cutover()
                self.stdout.write(
                    f"active=true email_ready={result['email_ready']} "
                    f"channel_unavailable={result['channel_unavailable']}"
                )
        except CTFCommunicationError as exc:
            raise CommandError(exc.code) from None
