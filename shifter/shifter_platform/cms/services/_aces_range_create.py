"""ACES-native range launch — the flag-gated parallel to ``create_range`` (#1479).

``create_aces_native_range`` launches a registered ACES package through the
native provisioning path: it reuses the create_range ownership / active-range /
audit helpers, persists the same CMS ``Request`` + ``RangeInstance`` bookkeeping
(so Mission Control visibility, active-range admission, and the
``range.status.updated`` -> ``apply_range_status`` flow all work uniformly, keyed
by ``request_id``), then drives the ACES backend + dispatch port instead of
cyberscript hydration. The ``RangeInstance`` carries ``range_spec=None``: ACES
ranges have no cyberscript spec (ADR-031-R2 -- no RangeSpec contamination).

``create_range_dispatch`` is the thin router product callers use: with the
SHIFTER_ACES_NATIVE_PROVISIONING flag off it always calls the cyberscript
``create_range`` (behaviour byte-identical to today); with the flag on it routes
a registered ACES scenario to the native path. The cyberscript ``create_range``
body is never modified (ADR-031-R2); this module only adds parallel functions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings

from cms.exceptions import CMSError
from cms.models import RangeInstance
from cms.services._range_create import (
    _assert_no_active_range,
    _assert_scenario_launchable,
    _audit_log_call,
    _reserve_active_range_slot,
    _set_range_instance_status,
    _validate_create_range_scenario,
    _validate_create_range_user,
    create_range,
)
from shared.audit import (
    AuditAction,
    AuditActorType,
    AuditEntityType,
)
from shared.enums import ResourceStatus

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from cms.models import AcesPackageSource, Request
    from shared.enums import RangeSource
    from shared.schemas.range import RangeContext

logger = logging.getLogger(__name__)

_NATIVE_DISABLED = "ACES-native provisioning is not enabled"


def _is_aces_scenario(scenario: str) -> bool:
    """Return True if ``scenario`` names a registered ACES package."""
    from cms.models import AcesPackageSource

    return AcesPackageSource.objects.filter(scenario_id=scenario).exists()


def _load_aces_source_or_raise(scenario: str) -> AcesPackageSource:
    """Return the AcesPackageSource for ``scenario`` or raise a clear CMSError."""
    from cms.models import AcesPackageSource

    try:
        return AcesPackageSource.objects.get(scenario_id=scenario)
    except AcesPackageSource.DoesNotExist:
        raise CMSError(f"No ACES package registered for scenario '{scenario}'") from None


def _dispatch_aces_package(request_id: UUID, user: User, package_ref: str) -> None:
    """Resolve, load, plan, and dispatch the ACES package; raise CMSError on failure."""
    from cms.aces.dispatch import CmsAcesDispatchPort
    from shared.aces.package_loader import (
        AcesPackageError,
        launch_aces_package,
        resolve_scenario_path,
    )

    try:
        scenario_path = resolve_scenario_path(package_ref, package_root=Path(settings.ACES_PACKAGE_ROOT))
    except AcesPackageError as exc:
        raise CMSError(f"ACES package could not be resolved: {exc}") from exc

    port = CmsAcesDispatchPort(user_id=user.id, request_id=str(request_id))
    try:
        result = launch_aces_package(scenario_path=scenario_path, port=port)
    except AcesPackageError as exc:
        raise CMSError(f"ACES package could not be launched: {exc}") from exc
    if not result.accepted:
        logger.warning("create_aces_native_range: dispatch not accepted request_id=%s", request_id)
        raise CMSError("ACES provisioning was not accepted")


def _audit_aces_range_provision(request_id: UUID, scenario: str, user: User, range_source: RangeSource) -> None:
    """Write the audit-log entry for a successful ACES-native launch."""
    _audit_log_call(
        entity_type=AuditEntityType.RANGE,
        entity_id=0,
        action=AuditAction.PROVISION,
        actor_type=AuditActorType.USER,
        actor_id=user.id,
        new_state={
            "request_id": str(request_id),
            "scenario": scenario,
            "provisioning": "aces-native",
            "range_source": range_source.value,
        },
        request_id=str(request_id),
    )


def _build_aces_range_context(request_id: UUID, scenario: str, user: User) -> RangeContext:
    """Build the RangeContext projection returned by the ACES-native launch."""
    from shared.schemas import RangeContext

    return RangeContext(
        request_id=request_id,
        range_id=None,
        scenario_id=scenario,
        user_id=user.id,
        status=ResourceStatus.PROVISIONING,
        instances=[],
        agent_name="",
    )


def create_aces_native_range(user: User, scenario: str, *, range_source: RangeSource | None = None) -> RangeContext:
    """Launch a registered ACES package through the native provisioning path.

    Flag-gated (raises if SHIFTER_ACES_NATIVE_PROVISIONING is off). Enforces the
    same user/active-range/launchability admission as ``create_range``, persists
    the CMS Request + RangeInstance bookkeeping, then dispatches the compiled
    ACES plan. On any dispatch failure the RangeInstance is marked FAILED and the
    error propagates.
    """
    from shared.enums import RangeSource

    if not settings.ACES_NATIVE_PROVISIONING_ENABLED:
        raise CMSError(_NATIVE_DISABLED)

    _validate_create_range_user(user)
    _validate_create_range_scenario(user, scenario)
    if range_source is None:
        range_source = RangeSource.MISSION_CONTROL

    _assert_no_active_range(user, range_source)
    _assert_scenario_launchable(scenario)
    source = _load_aces_source_or_raise(scenario)

    def _persist(cms_request: Request) -> RangeInstance:
        """Build the ACES RangeInstance (range_spec=None) for the reservation."""
        return RangeInstance.objects.create(
            request=cms_request,
            scenario_id=scenario,
            user_id=user.id,
            range_source=range_source.value,
            range_spec=None,
        )

    request_id, _cms_request, range_instance = _reserve_active_range_slot(user, range_source, _persist)

    try:
        _dispatch_aces_package(request_id, user, source.package_ref)
    except Exception:
        _set_range_instance_status(range_instance, ResourceStatus.FAILED)
        raise

    _audit_aces_range_provision(request_id, scenario, user, range_source)
    return _build_aces_range_context(request_id, scenario, user)


def create_range_dispatch(
    user: User,
    scenario: str,
    agents_by_os: dict[str, int],
    ngfw_enabled: bool = False,
    range_source: RangeSource | None = None,
) -> RangeContext:
    """Route a launch to the ACES-native or cyberscript path.

    With SHIFTER_ACES_NATIVE_PROVISIONING off, always calls the cyberscript
    ``create_range`` (byte-identical to today). With it on, a registered ACES
    scenario is launched through ``create_aces_native_range`` (``agents_by_os`` /
    ``ngfw_enabled`` do not apply to ACES packages); every other scenario stays
    on the cyberscript path.
    """
    if settings.ACES_NATIVE_PROVISIONING_ENABLED and _is_aces_scenario(scenario):
        return create_aces_native_range(user, scenario, range_source=range_source)
    return create_range(
        user,
        scenario,
        agents_by_os,
        ngfw_enabled=ngfw_enabled,
        range_source=range_source,
    )
