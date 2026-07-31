"""Persistence and projection builders for the cyberscript create path.

Extracted from ``_range_create`` so that module stays within its size budget
(SonarCloud ``python:S104``). These are pure builders on the create happy path:
one persists the ``RangeInstance`` row, the other projects the created range into
the template-safe ``RangeContext`` returned to callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from cms.models import RangeInstance
from shared.enums import ResourceStatus
from shared.schemas.persistence import wrap_persisted_spec

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from cms.models import AgentConfig, Request
    from cms.services._range_lease import RangeLease
    from shared.enums import RangeSource
    from shared.schemas.range import RangeContext, RangeSpec


def persist_range_instance_record(
    cms_request: Request,
    scenario: str,
    user: User,
    agents: dict[str, AgentConfig],
    range_spec: RangeSpec,
    lease: RangeLease,
    range_source: RangeSource,
) -> RangeInstance:
    """Persist the RangeInstance row tying the CMS Request to the hydrated spec."""
    # Store first agent for backward compatibility (field is nullable).
    first_agent = next(iter(agents.values()), None)
    return RangeInstance.objects.create(
        request=cms_request,
        scenario_id=scenario,
        user_id=user.id,
        # The projection inherits the request's authorized scope rather than
        # re-resolving it, so the two can never disagree (ADR-046-R3).
        workspace_id=cms_request.workspace_id,
        agent=first_agent,
        range_source=range_source.value,
        range_spec=wrap_persisted_spec("range_spec", range_spec),
        expires_at=lease.expires_at,
        maximum_expires_at=lease.maximum_expires_at,
    )


def build_range_context_for_create(
    request_id: UUID,
    scenario: str,
    user: User,
    range_spec: RangeSpec,
    agents: dict[str, AgentConfig],
) -> RangeContext:
    """Build the RangeContext projection returned by create_range."""
    from shared.schemas import InstanceContext, RangeContext

    instance_contexts = [
        InstanceContext(
            uuid=spec.uuid,
            name=spec.name or "",
            role=spec.role,
            os_type=spec.os_type,
            join_domain=spec.join_domain,
        )
        for spec in range_spec.all_instances
    ]
    agent_names = ", ".join(a.name for a in agents.values())
    return RangeContext(
        request_id=request_id,
        # Legacy field, use request_id for new ranges.
        range_id=None,
        scenario_id=scenario,
        user_id=user.id,
        status=ResourceStatus.PROVISIONING,
        instances=instance_contexts,
        agent_name=agent_names,
    )
