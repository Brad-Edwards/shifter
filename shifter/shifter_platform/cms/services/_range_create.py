"""Range provisioning: create_range plus its hydration/dispatch helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import IntegrityError, transaction

from cms.exceptions import CMSError
from cms.models import ACTIVE_RANGE_UNIQUE_CONSTRAINT, AgentConfig, RangeInstance

# Re-exported for existing importers (cms.services._raes_range_create, tests); the
# gate lives in its own module so _range_create stays within its size budget.
from cms.services._range_backend_admission import (
    _openvpn_backend_admitted,
    assert_backend_admitted,
)

# Re-exported for existing importers (cms.services._raes_range_create): the
# argument-shape and scenario admission validators live in their own module.
from cms.services._range_create_projection import (
    build_range_context_for_create,
    persist_range_instance_record,
)
from cms.services._range_create_validation import (
    _assert_scenario_launchable,
    _check_scenario_agent_requirements,
    _load_scenario_template_or_raise,
    _validate_create_range_agents_by_os,
    _validate_create_range_scenario,
    _validate_create_range_user,
)
from cms.services._range_remote_access import _build_remote_access_capability
from cms.services._range_workspace import (
    admit_workspace_launch,
    reauthorize_launch_workspace_locked,
    resolve_launch_workspace,
)
from shared.audit import (
    AuditAction,
    AuditActorType,
    AuditEntityType,
)
from shared.enums import ResourceStatus
from shared.range_instantiation_policy import InstantiationPurpose
from shared.schemas import RangeRef

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.contrib.auth.models import User

    from cms.models import Request
    from shared.enums import RangeSource
    from shared.range_instantiation_policy import BackendAdmission
    from shared.schemas import RequestSpec
    from shared.schemas.range import RangeContext, RangeSpec

logger = logging.getLogger(__name__)

# Authored, user-facing message for the single-active-range invariant. Shared by
# the friendly service pre-check (``_assert_no_active_range``) and the database
# constraint translation (``_reserve_active_range_slot``) so both admission
# rejections read identically and neither leaks driver/SQL detail (#307).
_ACTIVE_RANGE_MESSAGE = "You already have an active range. Please destroy it before creating a new one."


def _engine_create_range_call(
    request_spec: RequestSpec,
    backend_admission: BackendAdmission | None,
    remote_access_capability: dict[str, object] | None,
    workspace_id: int,
) -> RangeRef:
    """Late-bound call to ``cms.services.engine_create_range`` so test patches apply.

    ``backend_admission`` is the trusted #1348 admission result carried beside the
    RequestSpec (never inside it); the Engine persists the backend/purpose binding
    from it at create (#1666). ``workspace_id`` is the trusted #1325 tenancy
    scope and travels the same way (ADR-046-R3).
    """
    from cms import services as _cs

    if remote_access_capability is None:
        return _cs.engine_create_range(
            request_spec,
            backend_admission=backend_admission,
            workspace_id=workspace_id,
        )
    return _cs.engine_create_range(
        request_spec,
        backend_admission=backend_admission,
        remote_access_capability=remote_access_capability,
        workspace_id=workspace_id,
    )


def _audit_log_call(**kwargs: Any) -> None:  # NOSONAR
    """Late-bound call to ``cms.services.audit_log`` so test patches apply."""
    from cms import services as _cs

    _cs.audit_log(_cs.AuditEvent(**kwargs))


def _get_active_range_call(user: User, range_source: RangeSource | None = None) -> Any:  # NOSONAR
    """Look up active range through the package to honor test patches."""
    from cms import services as _cs

    return _cs.get_active_range(user, range_source)


def _get_agent_call(user: User, agent_id: int) -> AgentConfig:
    """Look up agent through the package to honor test patches."""
    from cms import services as _cs

    return _cs.get_agent(user, agent_id)


def _assert_no_active_range(user: User, range_source: RangeSource | None = None) -> None:
    """Raise CMSError if the user already has an active range for the given source.

    This is the friendly, fast pre-check for the common sequential case; it is a
    read-before-create and therefore races under concurrency. The authoritative
    backstop is the partial unique constraint enforced in
    :func:`_reserve_active_range_slot` (#307).
    """
    existing = _get_active_range_call(user, range_source)
    if existing:
        logger.warning(
            "create_range: user_id=%s already has active %s range request_id=%s",
            user.id,
            range_source,
            existing.range_id,
        )
        raise CMSError(_ACTIVE_RANGE_MESSAGE)


def _is_active_range_conflict(exc: IntegrityError) -> bool:
    """Return True iff ``exc`` violates the active-range unique constraint.

    Detection is backend-aware. PostgreSQL (production) names the violated
    constraint via ``exc.__cause__.diag.constraint_name`` -- the authoritative
    signal. SQLite (the fast test lane) reports the violated *columns* rather
    than the index name, so we fall back to matching the ``(user_id,
    range_source)`` column pair, which is unique to this constraint on
    ``RangeInstance``. Either way, only *this* constraint counts as an
    active-range conflict, so unrelated integrity errors still propagate (#307
    preflight: do not catch every ``IntegrityError`` as a duplicate range).
    """
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    if getattr(diag, "constraint_name", None) == ACTIVE_RANGE_UNIQUE_CONSTRAINT:
        return True
    message = str(exc)
    if ACTIVE_RANGE_UNIQUE_CONSTRAINT in message:
        return True
    return "user_id" in message and "range_source" in message


def _reserve_active_range_slot(
    user: User,
    range_source: RangeSource,
    persist_instance: Callable[[Request], RangeInstance],
    workspace_id: int,
    request_id: UUID | None = None,
) -> tuple[UUID, Request, RangeInstance]:
    """Atomically reserve the single active-range slot for ``(user, range_source)``.

    One transaction reauthorizes the workspace scope under the workspace mutex
    (ADR-046-R9), creates the CMS ``Request``, persists the ``RangeInstance``
    (built by ``persist_instance``), and sets it PROVISIONING. Holding the
    workspace row lock across the insert means a concurrent membership removal
    cannot leave a newly created range scoped somewhere its owner cannot reach.
    The partial unique constraint on ``(user_id, range_source)`` for active rows
    is the race-proof backstop: a losing concurrent caller's INSERT raises
    ``IntegrityError``, the whole transaction rolls back (so no orphan
    ``Request`` is left behind), and the *named* violation is translated into the
    authored active-range ``CMSError``. Unrelated integrity errors propagate.

    ``request_id`` is the caller's pre-minted correlation key (so workspace
    admission and reservation share one id); it is minted here when omitted.

    Cloud/engine dispatch MUST happen outside this call — never hold the
    transaction open across an Engine/RAES/broker call (#307 preflight).
    """
    from uuid import uuid4

    if request_id is None:
        request_id = uuid4()
    try:
        with transaction.atomic():
            reauthorize_launch_workspace_locked(user, workspace_id)
            cms_request = _create_cms_request(user, workspace_id, request_id)
            range_instance = persist_instance(cms_request)
            _set_range_instance_status(range_instance, ResourceStatus.PROVISIONING)
    except IntegrityError as exc:
        if _is_active_range_conflict(exc):
            logger.warning(
                "create_range: active-range constraint collision for user_id=%s range_source=%s",
                user.id,
                range_source.value,
            )
            raise CMSError(_ACTIVE_RANGE_MESSAGE) from exc
        raise
    return request_id, cms_request, range_instance


def _lookup_agents_by_os(user: User, agents_by_os: dict[str, int]) -> dict[str, AgentConfig]:
    """Resolve each agent ID to an AgentConfig owned by the user."""
    return {os_type: _get_agent_call(user, aid) for os_type, aid in agents_by_os.items()}


def _create_cms_request(user: User, workspace_id: int, request_id: UUID) -> Request:
    """Create the CMS Request row for the pre-minted ``request_id``."""
    from cms.models import Request
    from shared.enums import RequestType

    cms_request = Request.objects.create(
        request_id=request_id,
        request_type=RequestType.RANGE.value,
        user=user,
        workspace_id=workspace_id,
    )
    logger.info(
        "create_range: created CMS Request id=%s for user_id=%s",
        request_id,
        user.id,
    )
    return cms_request


def _dispatch_engine_range(
    request_id: UUID,
    user: User,
    range_spec: RangeSpec,
    backend_admission: BackendAdmission | None,
    remote_access_capability: dict[str, object] | None,
    workspace_id: int,
) -> None:
    """Dispatch range provisioning to engine for an already-owned CMS request.

    ``backend_admission`` (the trusted #1348 result) is carried beside the
    RequestSpec so the Engine persists the #1666 ownership binding at create.
    ``workspace_id`` is the trusted #1325 tenancy scope, carried the same way
    (beside, never inside, the spec) so Engine persists it in the range's create
    transaction without CMS reaching into Engine's models (ADR-046-R3).
    """
    from shared.schemas import RequestSpec

    request_spec = RequestSpec(
        request_id=request_id,
        user_id=user.id,
        items=[range_spec],
    )
    _engine_create_range_call(request_spec, backend_admission, remote_access_capability, workspace_id)


def _set_range_instance_status(range_instance: RangeInstance, status: ResourceStatus) -> None:
    """Persist CMS status for a range instance using the existing public vocabulary."""
    range_instance.status = status.value
    range_instance.save(update_fields=["status"])


def _audit_range_provision(
    request_id: UUID,
    scenario: str,
    user: User,
    agents: dict[str, AgentConfig],
    ngfw_enabled: bool,
) -> None:
    """Write the audit-log entry for a successful create_range request."""
    _audit_log_call(
        entity_type=AuditEntityType.RANGE,
        # Range ID not yet assigned at this point.
        entity_id=0,
        action=AuditAction.PROVISION,
        actor_type=AuditActorType.USER,
        actor_id=user.id,
        new_state={
            "request_id": str(request_id),
            "scenario": scenario,
            "agents": {os_type: a.name for os_type, a in agents.items()},
            "ngfw_enabled": ngfw_enabled,
        },
        request_id=str(request_id),
    )


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """Optional launch-shaping inputs, bundled to keep the create signatures small.

    ``ngfw_enabled`` and ``remote_access_teardown_at`` are the pre-existing
    request-shaping options; ``workspace_uuid`` is the #1327 public workspace
    selection (``None`` resolves the launcher's personal workspace). Grouping
    them means the internal create/dispatch functions stay within the
    parameter-count budget as the launch surface grows.
    """

    ngfw_enabled: bool = False
    remote_access_teardown_at: datetime | None = None
    workspace_uuid: str | UUID | None = None


def create_range(
    user: User,
    scenario: str,
    agents_by_os: dict[str, int],
    ngfw_enabled: bool = False,
    range_source: RangeSource | None = None,
    remote_access_teardown_at: datetime | None = None,
    workspace_uuid: str | UUID | None = None,
) -> RangeContext:
    """Validate, hydrate, and trigger range provisioning.

    The generic product facade. Every range it creates is live-fire and it takes
    no instantiation-purpose argument, so no in-process caller can escalate a
    normal launch onto the retained GDC substrate (ADR-030-R6). The dedicated
    authorized entry point for a non-user launch is
    ``cms.services.create_non_user_range``, which mints its purpose only after
    its own operator-authority gate.

    Args:
        user: User requesting the range
        scenario: Scenario ID (basic, ad_attack_lab)
        agents_by_os: Mapping of OS type to agent ID, e.g. {"windows": 123, "linux": 456}
        ngfw_enabled: Whether to deploy VM-Series NGFW inline
        range_source: Server-derived provenance label (RangeSource enum). Defaults to
            MISSION_CONTROL. Must be set by the product entry point (e.g. the CTF bridge
            passes RangeSource.CTF). Never user-supplied from a request body.
        remote_access_teardown_at: Trusted CTF event cleanup deadline used to
            build the CTF lease. Never accepted from an HTTP request body;
            Mission Control derives its lease entirely inside CMS.

    Returns:
        RangeContext: Template-safe projection of the created range

    Raises:
        TypeError: If user is None, invalid type, or parameters are
            invalid
        ValueError: If user has no ID (unsaved) or parameters are
            invalid
        CMSError: If scenario not found, agent not found, or
            requirements not met
    """
    return _create_range_impl(
        user,
        scenario,
        agents_by_os,
        range_source,
        InstantiationPurpose.LIVE_FIRE,
        LaunchOptions(
            ngfw_enabled=ngfw_enabled,
            remote_access_teardown_at=remote_access_teardown_at,
            workspace_uuid=workspace_uuid,
        ),
    )


def _create_range_impl(
    user: User,
    scenario: str,
    agents_by_os: dict[str, int],
    range_source: RangeSource | None,
    instantiation_purpose: InstantiationPurpose,
    options: LaunchOptions,
) -> RangeContext:
    """Shared cyberscript creation body, parameterized by minted launch authority.

    Not a product facade. ``instantiation_purpose`` is authority already minted by
    a caller that passed its own authorization gate -- either ``create_range``
    (permanently live-fire) or ``_non_user_range_launch.create_non_user_range``
    (operator-gated). ``assert_backend_admitted`` still re-checks the value.
    ``options`` bundles the optional launch-shaping inputs (see :class:`LaunchOptions`).
    """
    from cms.scenarios.hydrator import hydrate_scenario
    from shared.enums import RangeSource

    if range_source is None:
        range_source = RangeSource.MISSION_CONTROL

    _validate_create_range_user(user)
    _validate_create_range_scenario(user, scenario)
    _validate_create_range_agents_by_os(user, agents_by_os)

    from cms.services._range_lease import build_range_lease

    lease = build_range_lease(
        range_source,
        enforced_deadline=options.remote_access_teardown_at,
    )

    logger.debug(
        "create_range called for user_id=%s, scenario=%s, agents_by_os=%s, ngfw_enabled=%s, range_source=%s",
        user.id,
        scenario,
        agents_by_os,
        options.ngfw_enabled,
        range_source.value,
    )

    try:
        backend_admission = assert_backend_admitted(instantiation_purpose, range_source)
        _assert_no_active_range(user, range_source)

        _assert_scenario_launchable(scenario)
        scenario_template = _load_scenario_template_or_raise(scenario)
        requirements = scenario_template.get_agent_requirements()
        _check_scenario_agent_requirements(scenario, requirements, agents_by_os)

        agents = _lookup_agents_by_os(user, agents_by_os)
        range_spec = hydrate_scenario(scenario, user.id, agents)
        remote_access_capability = _build_remote_access_capability(
            range_spec,
            lease.maximum_expires_at,
            backend_admitted=_openvpn_backend_admitted(backend_admission),
            required=range_source is RangeSource.CTF,
        )

        def _persist(cms_request: Request) -> RangeInstance:
            """Build the RangeInstance for the reservation from the hydrated spec."""
            return persist_range_instance_record(
                cms_request,
                scenario,
                user,
                agents,
                range_spec,
                lease,
                range_source,
            )

        from uuid import uuid4

        request_id = uuid4()
        workspace_id = resolve_launch_workspace(user, options.workspace_uuid)
        admit_workspace_launch(
            workspace_id=workspace_id,
            user=user,
            range_source=range_source,
            instantiation_purpose=instantiation_purpose,
            correlation_key=request_id,
        )
        _request_id, _cms_request, range_instance = _reserve_active_range_slot(
            user, range_source, _persist, workspace_id, request_id
        )
        try:
            _dispatch_engine_range(
                request_id,
                user,
                range_spec,
                backend_admission,
                remote_access_capability,
                workspace_id,
            )
        except Exception:
            _set_range_instance_status(range_instance, ResourceStatus.FAILED)
            raise
        _audit_range_provision(request_id, scenario, user, agents, options.ngfw_enabled)

        logger.debug(
            "create_range completed: request_id=%s, scenario=%s, user_id=%s, range_source=%s",
            request_id,
            scenario,
            user.id,
            range_source.value,
        )
        return build_range_context_for_create(request_id, scenario, user, range_spec, agents)

    except (TypeError, ValueError, CMSError):
        raise
    except Exception:
        logger.exception("Error in create_range for user_id=%s", user.id)
        raise
