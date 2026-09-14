"""Run actual contract checks for a post-setup private pack installation."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from cms.services import validate_registered_pack_conformance
from shared.exceptions import ValidationError


class Command(BaseCommand):
    help = "Validate immutable registered pack bytes and the supported backend contract."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)
        parser.add_argument("--scenario-id", required=True)
        parser.add_argument("--expected-package-digest", required=True)

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options["actor"]).first()
        try:
            status = validate_registered_pack_conformance(
                user=user,
                scenario_id=options["scenario_id"],
                expected_package_digest=options["expected_package_digest"],
            )
        except (ValidationError, PermissionDenied, TypeError, ValueError) as exc:
            raise CommandError("Registered pack conformance validation failed") from exc
        self.stdout.write(status)
