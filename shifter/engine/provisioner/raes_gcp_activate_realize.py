"""Live GCE realization of a claimant's fresh access at warm activation (#28).

The claimed generation's infrastructure is already realized; activation replaces
only the *access* surface for the claimant. It reuses the exact apply realization
path -- ``raes_gcp_apply.realize_access_on_existing_cell`` -- whose instance ensure
is idempotent, so no infrastructure is recreated: the guest account credentials are
re-established freshly (the caller scrubbed the pre-claim secrets first) and the
claimant's participant access is published. The bounded member/access rows the
Engine applier persists are projected from the realized instance outputs (secret
*references* only, never credential values).

The claimant's VPN identity is generation-fenced and regenerates for the new owner
against the rotated (activate) operation generation; the pre-claim VPN generation
was already deleted by the scrub step, so activation does not re-mint a VPN profile
here.

This module performs live GCE work; its efficacy is verified on a real range (the
repository's verification norm for provisioner cloud effects). It fails closed: any
realization error raises and the orchestrator retires the generation rather than
hand it over.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from shared.warm_pool.activation_input import ActivationInput

from cloud.exceptions import CloudError
from raes_gcp_apply import RaesGceApplyOptions, realize_access_on_existing_cell
from raes_plan import parse_plan
from raes_range_ops import _realized_members, _registry_resolver

logger = logging.getLogger(__name__)


class ActivationRealizationError(CloudError):
    """The claimant's fresh access could not be realized on the claimed generation."""


def realize_claimant_access_on_cell(activation: ActivationInput, activate_generation: UUID) -> list[dict[str, Any]]:
    """Rotate credentials and realize the claimant's participant access; return members.

    Fails closed (raising :class:`ActivationRealizationError`) on any realization
    error so a claimant is never handed a range whose access could not be fully
    re-established for them.
    """
    operation_input = activation.raes_input
    raes_plan = parse_plan(operation_input.plan)
    try:
        instance_outputs = realize_access_on_existing_cell(
            str(activate_generation),
            activation.legacy_range_id,
            raes_plan,
            _registry_resolver(operation_input),
            options=RaesGceApplyOptions(egress_mode=operation_input.egress_mode),
            access_bindings=operation_input.access_binding_transport(),
        )
    except Exception as exc:
        raise ActivationRealizationError(
            f"warm activation could not realize claimant access: {type(exc).__name__}"
        ) from None
    return _realized_members({"instances": instance_outputs})
