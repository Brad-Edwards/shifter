"""Apply pending provisioner operation results from the result inbox (shadow).

ADR-043 Phase 2 (#1834). Runs the engine-owned shadow applier: it claims PENDING
``OperationResultInbox`` rows and records a validation disposition. It never
mutates domain state, audit, or the range event outbox — direct provisioner SQL
remains authoritative. Deployed as a portal worker (under the portal runtime
role, not the provisioner) alongside the other management-command workers.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
import time
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from engine.services import apply_pending_operation_results

logger = logging.getLogger(__name__)
HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "worker-operation-result-applier-heartbeat"


class Command(BaseCommand):
    """Evaluate due operation-result inbox rows while maintaining worker liveness."""

    help = "Record shadow dispositions for pending provisioner operation results."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register batch and polling options for the applier worker."""
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--loop", action="store_true", default=False)
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args: Any, **options: Any) -> None:
        """Apply once or continuously according to the command options."""
        while True:
            self._touch_heartbeat()
            evaluated = apply_pending_operation_results(batch_size=options["batch_size"])
            self.stdout.write(f"Evaluated {evaluated} operation results")
            if not options["loop"]:
                return
            time.sleep(options["interval"])

    @staticmethod
    def _touch_heartbeat() -> None:
        """Refresh the applier liveness marker when the filesystem permits."""
        with contextlib.suppress(OSError):
            HEARTBEAT_FILE.touch()
