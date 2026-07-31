"""RAES-native range launch — the flag-gated parallel to ``create_range`` (#1479).

``create_raes_native_range`` launches a registered RAES package through the
native provisioning path: it reuses the create_range ownership / active-range /
audit helpers, persists the same CMS ``Request`` + ``RangeInstance`` bookkeeping
(so Mission Control visibility, active-range admission, and the
``range.status.updated`` -> ``apply_range_status`` flow all work uniformly, keyed
by ``request_id``), then drives the RAES backend + dispatch port instead of
cyberscript hydration. The ``RangeInstance`` carries ``range_spec=None``: RAES
ranges have no cyberscript spec (ADR-031-R2 -- no RangeSpec contamination).

``create_range_dispatch`` is the thin router product callers use: with the
SHIFTER_RAES_NATIVE_PROVISIONING flag off it always calls the cyberscript
``create_range`` (behaviour byte-identical to today); with the flag on it routes
a registered RAES scenario to the native path. The cyberscript ``create_range``
body is never modified (ADR-031-R2); this module only adds parallel functions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings

from cms.exceptions import CMSError
from cms.models import RangeInstance
from cms.services._range_backend_admission import assert_backend_admitted
from cms.services._range_create import (
    _assert_no_active_range,
    _assert_scenario_launchable,
    _audit_log_call,
    _create_range_impl,
    _reserve_active_range_slot,
    _set_range_instance_status,
    _validate_create_range_scenario,
    _validate_create_range_user,
)
from cms.services._range_workspace import admit_workspace_launch, resolve_launch_workspace
from shared.audit import (
    AuditAction,
    AuditActorType,
    AuditEntityType,
)
from shared.enums import ResourceStatus
from shared.range_instantiation_policy import InstantiationPurpose

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from cms.models import RaesPackageSource, Request
    from shared.enums import RangeSource
    from shared.range_instantiation_policy import BackendAdmission
    from shared.schemas.range import RangeContext

logger = logging.getLogger(__name__)

_NATIVE_DISABLED = "RAES-native provisioning is not enabled"
_OBJECT_SOURCE_KIND = "object"
# The only range backend raes_range_ops can realize today (#1354).
_RAES_REALIZED_BACKEND = "gce"


def _load_raes_source_or_raise(scenario: str) -> RaesPackageSource:
    """Return the RaesPackageSource for ``scenario`` or raise a clear CMSError."""
    from cms.models import RaesPackageSource

    try:
        return RaesPackageSource.objects.get(scenario_id=scenario)
    except RaesPackageSource.DoesNotExist:
        raise CMSError(f"No RAES package registered for scenario '{scenario}'") from None


def _dispatch_raes_package(
    request_id: UUID,
    user: User,
    source: RaesPackageSource,
    backend_admission: BackendAdmission | None,
    workspace_id: int,
) -> None:
    """Resolve, verify, load, plan, and dispatch one registered RAES pack.

    Routes on ``source.source_kind``: a ``repo`` pack resolves under
    ``RAES_PACKAGE_ROOT``; an ``object`` pack resolves through the object-storage
    launch resolver (#1567). Both paths end in the same digest-verified,
    canonical-launch tail (:func:`_launch_pack`). ``backend_admission`` (the
    trusted #1348 result) is threaded to the dispatch port so the Engine binds
    the #1666 ownership fields at create; ``workspace_id`` (the trusted #1325
    tenancy scope) rides the same way so the RAES path scopes ranges exactly like
    the cyberscript path (ADR-046-R3).
    """
    if source.source_kind == _OBJECT_SOURCE_KIND:
        _dispatch_object_raes_package(request_id, user, source, backend_admission, workspace_id)
    else:
        _dispatch_repo_raes_package(request_id, user, source, backend_admission, workspace_id)


def _dispatch_repo_raes_package(
    request_id: UUID,
    user: User,
    source: RaesPackageSource,
    backend_admission: BackendAdmission | None,
    workspace_id: int,
) -> None:
    """Resolve a repo pack under ``RAES_PACKAGE_ROOT``, verify its digest, launch."""
    from cms.scenarios.pack_validation import PackDigestError, verify_pack_digest
    from shared.raes.package_loader import RaesPackageError, resolve_pack_root

    try:
        pack_root = resolve_pack_root(source.package_ref, package_root=Path(settings.RAES_PACKAGE_ROOT))
    except RaesPackageError as exc:
        raise CMSError(f"RAES package could not be resolved: {exc}") from exc
    try:
        digest_matches = verify_pack_digest(pack_root, source.package_digest)
    except PackDigestError as exc:
        raise CMSError("RAES pack content identity could not be verified") from exc
    if not digest_matches:
        raise CMSError("RAES pack content digest no longer matches registration")
    _launch_pack(request_id, user, pack_root, backend_admission, workspace_id)


def _dispatch_object_raes_package(
    request_id: UUID,
    user: User,
    source: RaesPackageSource,
    backend_admission: BackendAdmission | None,
    workspace_id: int,
) -> None:
    """Stage an object-backed pack, bind its identity + digest, then launch.

    Object rows are registered without content validation or digest binding
    (#1578), so this resolver provides the equivalent identity guarantees repo
    packs get (ADR-034-R5): it downloads the single immutable archive named by
    ``package_ref`` into a private temp dir, safely extracts it, re-runs the
    upstream pack contract validation, asserts the pack identity matches the
    registered ``scenario_id``, and verifies the canonical ``package_digest`` --
    all before SDL resolution, planning, or dispatch. The staged directory is
    always cleaned up by the resolver context manager.
    """
    from cms.scenarios.pack_validation import (
        PackDigestError,
        PackValidationError,
        validate_pack,
        verify_pack_digest,
    )
    from shared.cloud import get_object_storage
    from shared.raes.object_source import stage_object_pack
    from shared.raes.package_loader import RaesPackageError

    bucket = str(getattr(settings, "RAES_PACKAGE_BUCKET", "") or "").strip()
    if not bucket:
        raise CMSError("Object-backed RAES packages are not available: no package bucket is configured")

    try:
        with stage_object_pack(
            storage=get_object_storage(),
            bucket=bucket,
            key=_object_package_key(source.package_ref),
            max_archive_bytes=settings.RAES_PACKAGE_MAX_ARCHIVE_BYTES,
            max_uncompressed_bytes=settings.RAES_PACKAGE_MAX_UNCOMPRESSED_BYTES,
            max_entries=settings.RAES_PACKAGE_MAX_ENTRIES,
        ) as pack_root:
            try:
                validated_name = validate_pack(pack_root)
            except PackValidationError as exc:
                raise CMSError("RAES pack failed validation") from exc
            if validated_name != source.scenario_id:
                raise CMSError("RAES pack identity does not match the registered scenario")
            try:
                digest_matches = verify_pack_digest(pack_root, source.package_digest)
            except PackDigestError as exc:
                raise CMSError("RAES pack content identity could not be verified") from exc
            if not digest_matches:
                raise CMSError("RAES pack content digest no longer matches registration")
            _launch_pack(request_id, user, pack_root, backend_admission, workspace_id)
    except RaesPackageError as exc:
        raise CMSError(f"RAES object package could not be resolved: {exc}") from exc


def _object_package_key(package_ref: str) -> str:
    """Join the configured object-package prefix with the row's ``package_ref``."""
    prefix = str(getattr(settings, "RAES_PACKAGE_PREFIX", "") or "").strip().strip("/")
    ref = package_ref.strip().lstrip("/")
    return f"{prefix}/{ref}" if prefix else ref


def _launch_pack(
    request_id: UUID,
    user: User,
    pack_root: Path,
    backend_admission: BackendAdmission | None,
    workspace_id: int,
) -> None:
    """Select the single SDL entry, dispatch through the port, assert acceptance."""
    from cms.raes.dispatch import CmsRaesDispatchPort
    from shared.raes.package_loader import RaesPackageError, launch_raes_package, resolve_pack_scenario_path

    try:
        scenario_path = resolve_pack_scenario_path(pack_root)
    except RaesPackageError as exc:
        raise CMSError(f"RAES package could not be resolved: {exc}") from exc

    port = CmsRaesDispatchPort(
        user_id=user.id,
        request_id=str(request_id),
        backend_admission=backend_admission,
        pack_root=pack_root,
        workspace_id=workspace_id,
    )
    try:
        result = launch_raes_package(scenario_path=scenario_path, port=port)
    except RaesPackageError as exc:
        raise CMSError(f"RAES package could not be launched: {exc}") from exc
    if not result.accepted:
        logger.warning("create_raes_native_range: dispatch not accepted request_id=%s", request_id)
        raise CMSError("RAES provisioning was not accepted")


def _audit_raes_range_provision(request_id: UUID, scenario: str, user: User, range_source: RangeSource) -> None:
    """Write the audit-log entry for a successful RAES-native launch."""
    _audit_log_call(
        entity_type=AuditEntityType.RANGE,
        entity_id=0,
        action=AuditAction.PROVISION,
        actor_type=AuditActorType.USER,
        actor_id=user.id,
        new_state={
            "request_id": str(request_id),
            "scenario": scenario,
            "provisioning": "raes-native",
            "range_source": range_source.value,
        },
        request_id=str(request_id),
    )


def _build_raes_range_context(request_id: UUID, scenario: str, user: User) -> RangeContext:
    """Build the RangeContext projection returned by the RAES-native launch."""
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


def _assert_raes_adapter_supports(backend_admission: BackendAdmission | None) -> None:
    """Refuse an RAES launch on an admitted backend that has no RAES adapter (#1354).

    Policy admission and adapter availability are independent gates (ADR-030
    preflight). ``raes_range_ops`` realizes GCE range cells only, so a non-user
    purpose the policy permits on the retained GDC substrate must still fail
    closed here -- before reservation and dispatch -- rather than binding ``gdc``
    and then running the hard-coded GCE adapter.
    """
    if backend_admission is None or backend_admission.backend == _RAES_REALIZED_BACKEND:
        return
    raise CMSError(
        f"RAES-native provisioning has no realization adapter for range backend "
        f"'{backend_admission.backend}'; only the GCE VM range-cell backend is implemented.",
        details={"code": "unsupported-capability"},
    )


def create_raes_native_range(
    user: User,
    scenario: str,
    *,
    range_source: RangeSource | None = None,
    workspace_uuid: str | UUID | None = None,
) -> RangeContext:
    """Launch a registered RAES package through the native provisioning path.

    The generic RAES product facade, permanently live-fire and taking no
    instantiation-purpose argument (ADR-030-R6). The operator-gated non-user
    entry point is ``cms.services.create_non_user_range``.

    Flag-gated (raises if SHIFTER_RAES_NATIVE_PROVISIONING is off). Enforces the
    same user/active-range/launchability admission as ``create_range``, persists
    the CMS Request + RangeInstance bookkeeping, then dispatches the compiled
    RAES plan. On any dispatch failure the RangeInstance is marked FAILED and the
    error propagates.
    """
    return _create_raes_native_range_impl(
        user,
        scenario,
        range_source=range_source,
        instantiation_purpose=InstantiationPurpose.LIVE_FIRE,
        workspace_uuid=workspace_uuid,
    )


def _create_raes_native_range_impl(
    user: User,
    scenario: str,
    *,
    range_source: RangeSource | None,
    instantiation_purpose: InstantiationPurpose,
    raes_source_id: str | None = None,
    workspace_uuid: str | UUID | None = None,
) -> RangeContext:
    """Shared RAES creation body, parameterized by minted launch authority.

    Not a product facade; see ``_range_create._create_range_impl``. ``scenario``
    is the stable public id used for persistence, correlation, and audit;
    ``raes_source_id`` (default: ``scenario``) is the internal registered
    package-source actually loaded, so a routed public id (``polaris``) launches
    its distinct source (``polaris-raes``) while the range still correlates by the
    public id (ADR-031-R5/R6).
    """
    from shared.enums import RangeSource

    if not settings.RAES_NATIVE_PROVISIONING_ENABLED:
        raise CMSError(_NATIVE_DISABLED)

    _validate_create_range_user(user)
    _validate_create_range_scenario(user, scenario)
    if range_source is None:
        range_source = RangeSource.MISSION_CONTROL
    from cms.services._range_lease import build_range_lease

    lease = build_range_lease(range_source)

    backend_admission = assert_backend_admitted(instantiation_purpose, range_source)
    _assert_raes_adapter_supports(backend_admission)
    _assert_no_active_range(user, range_source)
    _assert_scenario_launchable(scenario)
    source = _load_raes_source_or_raise(raes_source_id or scenario)

    def _persist(cms_request: Request) -> RangeInstance:
        """Build the RAES RangeInstance (range_spec=None) for the reservation."""
        return RangeInstance.objects.create(
            request=cms_request,
            scenario_id=scenario,
            user_id=user.id,
            # Inherit the request's authorized scope rather than re-resolving it,
            # so the two projections can never disagree (ADR-046-R3).
            workspace_id=cms_request.workspace_id,
            range_source=range_source.value,
            range_spec=None,
            expires_at=lease.expires_at,
            maximum_expires_at=lease.maximum_expires_at,
        )

    from uuid import uuid4

    request_id = uuid4()
    workspace_id = resolve_launch_workspace(user, workspace_uuid)
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
        _dispatch_raes_package(request_id, user, source, backend_admission, workspace_id)
    except Exception:
        _set_range_instance_status(range_instance, ResourceStatus.FAILED)
        raise

    _audit_raes_range_provision(request_id, scenario, user, range_source)
    return _build_raes_range_context(request_id, scenario, user)


def create_range_dispatch(
    user: User,
    scenario: str,
    agents_by_os: dict[str, int],
    ngfw_enabled: bool = False,
    range_source: RangeSource | None = None,
    remote_access_teardown_at: datetime | None = None,
    workspace_uuid: str | UUID | None = None,
) -> RangeContext:
    """Route a launch to the RAES-native or cyberscript path.

    The thin product router, permanently live-fire (ADR-030-R6). With
    SHIFTER_RAES_NATIVE_PROVISIONING off, always calls the cyberscript
    ``create_range`` (byte-identical to today). With it on, a registered RAES
    scenario is launched through ``create_raes_native_range`` (``agents_by_os`` /
    ``ngfw_enabled`` do not apply to RAES packages); every other scenario stays
    on the cyberscript path.

    ``workspace_uuid`` is the optional public workspace selection (ADR-046-R9),
    threaded to whichever create path runs. Server-derived callers (e.g. the CTF
    bridge) omit it, so their ranges bind to the launcher's personal workspace.
    """
    return dispatch_range_launch(
        user,
        scenario,
        agents_by_os,
        ngfw_enabled=ngfw_enabled,
        range_source=range_source,
        remote_access_teardown_at=remote_access_teardown_at,
        instantiation_purpose=InstantiationPurpose.LIVE_FIRE,
        workspace_uuid=workspace_uuid,
    )


def dispatch_range_launch(
    user: User,
    scenario: str,
    agents_by_os: dict[str, int],
    *,
    ngfw_enabled: bool,
    range_source: RangeSource | None,
    remote_access_teardown_at: datetime | None,
    instantiation_purpose: InstantiationPurpose,
    workspace_uuid: str | UUID | None = None,
) -> RangeContext:
    """Shared RAES/cyberscript routing body, parameterized by minted launch authority.

    Not a product facade; see ``_range_create._create_range_impl``. Internal to
    the CMS create seam -- ``cms.services`` exports the two facades that wrap it,
    never this function.
    """
    from cms.scenarios.cutover import resolve_launch

    if settings.RAES_NATIVE_PROVISIONING_ENABLED:
        resolution = resolve_launch(scenario)
        if resolution.is_raes:
            if resolution.raes_source_id is None:
                # A routed internal source id is not offered as a direct launch choice.
                raise CMSError(f"Scenario '{scenario}' is not available for launch")
            if remote_access_teardown_at is not None:
                raise CMSError("The RAES-native range adapter does not support CTF OpenVPN access")
            return _create_raes_native_range_impl(
                user,
                scenario,
                range_source=range_source,
                instantiation_purpose=instantiation_purpose,
                raes_source_id=resolution.raes_source_id,
                workspace_uuid=workspace_uuid,
            )
    return _create_range_impl(
        user,
        scenario,
        agents_by_os,
        ngfw_enabled,
        range_source,
        remote_access_teardown_at,
        instantiation_purpose,
        workspace_uuid=workspace_uuid,
    )
