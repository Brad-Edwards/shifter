"""Dispatch seam for the ACES-native provisioning backend (ADR-031, ADR-024).

The ACES RuntimeTarget backend (:mod:`shared.aces.runtime_target`) interprets a
compiled ACES ``ProvisioningPlan`` into the neutral :class:`ProvisioningSpec` and
then dispatches it through this injected port. The port is where the DB /
engine / provisioner side effects live, so ``shared`` never imports ``cms`` or
``engine`` (ADR-024): the concrete implementation is constructed on the
realization side (``cms.aces``) with the per-launch context (the Shifter
``request_id`` and any owner/agent context) and handed to the backend.

Keeping this module free of ``aces_*`` imports lets the realization side depend
on the seam without pulling the SDL tooling (ADR-031-R1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from shared.aces.provisioning_spec import ProvisioningSpec

__all__ = [
    "ShifterDispatchResult",
    "ShifterProvisioningDispatchPort",
]


@dataclass(frozen=True)
class ShifterDispatchResult:
    """Outcome of dispatching a :class:`ProvisioningSpec` for realization.

    Carries IDs and status only — never a raw spec, provider payload, or secret.
    ``accepted`` is whether the range was accepted for provisioning (the receipt
    boundary); the realized/ready state converges asynchronously and is reported
    later through the operation-status/runtime-snapshot sidecar path.
    """

    request_id: str
    accepted: bool
    status: str
    range_id: str | None = None
    detail: str | None = None


@runtime_checkable
class ShifterProvisioningDispatchPort(Protocol):
    """Injected seam that realizes a :class:`ProvisioningSpec` (ADR-024).

    The concrete implementation (``cms.aces``) owns the per-launch Shifter
    ``request_id`` and performs the request creation, spec persistence,
    operation-receipt sidecar write, and provisioning dispatch. The backend only
    interprets and validates; it never imports ``cms``/``engine`` itself.
    """

    @property
    def request_id(self) -> str:
        """The Shifter request id this dispatch is keyed by (operation key)."""
        ...

    def realize(self, spec: ProvisioningSpec) -> ShifterDispatchResult:
        """Persist + dispatch ``spec`` for provisioning; return IDs/status only."""
        ...
