"""CMS-side ACES range-realization port (issue #1262).

Concrete implementation of
:class:`shared.aces.runtime_target.ShifterRangeRealizationPort`. Translates a
validated :class:`~shared.aces.runtime_target.ShifterProvisioningIntent` into
a wrapped Shifter ``RangeSpec`` through the incumbent hydration path
(``hydrate_scenario`` -> ``wrap_persisted_spec``) -- the same path
``cms.services.create_range`` uses to build the spec it persists and
dispatches.

This module intentionally does **not** persist a ``RangeInstance`` row and
does **not** dispatch to ``cms.services.create_range`` / ``engine.services.create_range``
/ ``engine.ecs``. Those steps flip catalog launchability and start live
provisioning, which are out of scope for this translation-boundary slice (see
issues #1263 / #1264). It imports only ``shared``/``cms``/``engine``; it never
imports an ``aces_*`` SDL package directly (ADR-024) -- that stays confined to
``shared/aces/manifest.py`` and ``shared/aces/runtime_target.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cms.scenarios.hydrator import hydrate_scenario
from cms.services import get_agent
from shared.aces.runtime_target import ShifterProvisioningIntent, ShifterRealizationResult
from shared.schemas.persistence import wrap_persisted_spec

if TYPE_CHECKING:
    from django.contrib.auth.models import User

__all__ = ["CmsRangeRealizationPort"]


@dataclass(frozen=True)
class CmsRangeRealizationPort:
    """Realizes a :class:`ShifterProvisioningIntent` via the incumbent hydration path.

    ``user`` and ``agents_by_os`` are the CMS launch context (the same shape
    ``cms.services.create_range`` takes) supplied by the caller that
    constructs this port -- the ACES ``ProvisioningPlan`` never carries a
    Shifter agent-catalog identifier, so the concrete port, not the plan, is
    the source of that mapping.
    """

    user: User
    agents_by_os: dict[str, int]

    def realize(self, intent: ShifterProvisioningIntent) -> ShifterRealizationResult:
        """Hydrate and wrap a Shifter spec for ``intent``; return IDs/status only.

        Raises whatever ``get_agent``/``hydrate_scenario`` raise (typically
        ``cms.exceptions.CMSError`` for an unknown scenario or an
        unowned/missing agent) -- callers on the ``shared`` side of the ADR-024
        boundary catch broad exceptions and turn them into ACES diagnostics,
        so this port is free to let real business errors propagate rather than
        swallow them.
        """
        agents = {os_type: get_agent(self.user, agent_id) for os_type, agent_id in self.agents_by_os.items()}
        range_spec = hydrate_scenario(intent.scenario_ref, self.user.id, agents)
        # Proves the translation produces a genuinely valid wrapped spec (the
        # same call ``cms.services.create_range`` makes before persisting);
        # the wrapped payload itself is discarded -- this slice never persists
        # or dispatches it.
        wrap_persisted_spec("range_spec", range_spec)
        # ``RangeSpec.uuid`` is typed Optional on the shared base spec (assigned
        # during hydration for other spec kinds), but ``hydrate_scenario``
        # always assigns a fresh uuid4 for every RangeSpec it returns.
        assert range_spec.uuid is not None, "hydrate_scenario must assign RangeSpec.uuid"
        return ShifterRealizationResult(range_uuid=range_spec.uuid, status="translated")
