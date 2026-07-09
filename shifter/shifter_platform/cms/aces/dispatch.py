"""CMS-side concrete ACES provisioning dispatch port (ADR-031, ADR-024).

Concrete implementation of
:class:`shared.aces.dispatch_port.ShifterProvisioningDispatchPort`. It hands the
neutral :class:`~shared.aces.provisioning_spec.ProvisioningSpec` to the engine
service seam (:func:`engine.services.create_aces_range`), which persists it keyed
by ``request_id``, writes the operation-receipt sidecar, and dispatches the
provisioner ``aces-range`` task.

Conformance boundary (ADR-031-R1): the ACES code path imports **no** cyberscript
module and carries **no** cyberscript semantics. The persisted artifact is the
bare, self-describing ``ProvisioningSpec`` JSON (it carries its own
``contract_version`` / ``profile`` discriminator) -- not a cyberscript persisted
envelope. This module imports only ``shared.aces`` and the public
``engine.services`` facade.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.services import create_aces_range
from shared.aces.dispatch_port import ShifterDispatchResult
from shared.aces.provisioning_spec import ProvisioningSpec

__all__ = ["CmsAcesDispatchPort"]


@dataclass(frozen=True)
class CmsAcesDispatchPort:
    """Realize a ProvisioningSpec through the engine ACES range service.

    Constructed per launch with the CMS launch context (the owning user id and
    the Shifter ``request_id`` the operation is keyed by). The RuntimeTarget
    backend builds the spec with ``request_id`` and calls :meth:`realize`.
    """

    user_id: int
    request_id: str

    def realize(self, spec: ProvisioningSpec) -> ShifterDispatchResult:
        ref = create_aces_range(
            request_id=spec.request_id,
            user_id=self.user_id,
            provisioning_spec=spec.model_dump(mode="json"),
        )
        return ShifterDispatchResult(
            request_id=ref.request_id, accepted=ref.accepted, status=ref.status, range_id=ref.range_id
        )
