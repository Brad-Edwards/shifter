"""Readiness contract for a preconfigured machine-image range host."""

from __future__ import annotations

from typing import Any, ClassVar

from .base import SetupStep

_WAIT_FOR_READY_SCRIPT = r"""#!/bin/bash
set -euo pipefail
container="{{ participant_container_name }}"
deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
    if [ -e "/run/shifter/preconfigured-range-host.ready" ] \
        && [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" = "true" ] \
        && timeout 3 bash -c "</dev/tcp/127.0.0.1/3389" 2>/dev/null; then
        echo "Preconfigured range host is ready"
        exit 0
    fi
    sleep 10
done
echo "FATAL: preconfigured range host readiness timed out" >&2
exit 1
"""


class PreconfiguredMachineHostPlan:
    """Wait for the image-owned nested workload and participant RDP endpoint."""

    name = "preconfigured_machine_host_readiness"
    steps: ClassVar[list[SetupStep]] = [
        SetupStep(
            name="wait_for_preconfigured_machine_host",
            script=_WAIT_FOR_READY_SCRIPT,
            timeout_seconds=930,
        )
    ]
    verify_step: ClassVar[SetupStep | None] = None

    def get_context(self, instance: Any) -> dict[str, Any]:
        """Return the validated profile-selected container name."""
        return {"participant_container_name": instance["gcp_participant_container_name"]}


__all__ = ["PreconfiguredMachineHostPlan"]
