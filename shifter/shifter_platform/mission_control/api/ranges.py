"""Range and catalog DRF views for Mission Control."""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from django.contrib.auth.models import User
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.request import Request
from rest_framework.response import Response

from cms.services import (
    RetryKeyConflict,
    WorkspaceLaunchDenied,
    WorkspaceLaunchQuotaExceeded,
    bind_first_use_launch,
    get_active_range,
    get_mission_control_range_lease,
    get_range_by_request_id,
    has_mission_control_openvpn_profile,
    list_mission_control_range_history,
    resolve_retry_recovery,
)
from cms.services import (
    create_range_dispatch as cms_create_range,
)
from cms.services import (
    extend_mission_control_range as cms_extend_mission_control_range,
)
from cms.services import (
    get_agent as cms_get_agent,
)
from cms.services import (
    list_agents as cms_list_agents,
)
from cms.services import (
    list_launchable_scenarios as cms_list_launchable_scenarios,
)
from cms.services import (
    max_agent_file_size_bytes as cms_max_agent_file_size_bytes,
)
from mission_control.api._base import (
    MissionControlAPIView,
    MissionControlReadAPIView,
    _range_write_permission,
    _raw_request,
    _validated,
)
from mission_control.api.permissions import HasMissionControlActor, block_participant_lifecycle_permission
from mission_control.api.rate_limit import RangeLaunchRateThrottle
from mission_control.api.serializers import (
    AgentListResponseSerializer,
    CurrentRangeResponseSerializer,
    LaunchRangeResponseSerializer,
    LaunchRangeSerializer,
    RangeHistoryResponseSerializer,
    RangeHistorySerializer,
    RangeLeaseResponseSerializer,
    RangeLifecycleSerializer,
    ScenarioListResponseSerializer,
    SuccessResponseSerializer,
)
from mission_control.utils import build_connection_urls
from mission_control.views._common import _audit_range_lifecycle
from shared.api.permissions import IsAuthenticatedSessionOrApiToken
from shared.api.schema import ApiErrorSerializer
from shared.audit import AuditAction
from shared.errors import classify_user_message
from shared.exceptions import CMSError
from shared.log_sanitize import safe_log_value
from shared.raes.presentation import build_range_participant_runtime_projection, build_range_raes_projection
from shared.range_visibility import filter_visible_instances

logger = logging.getLogger(__name__)


class CurrentRangeView(MissionControlReadAPIView):
    """Return the current user's active range."""

    @extend_schema(responses=CurrentRangeResponseSerializer, operation_id="api_v1_mission_control_range_retrieve")
    def get(self, request: Request) -> Response:
        """Return the active range and connection URLs for the request user."""
        actor = self.actor_user()
        active_range = get_active_range(actor)
        if not active_range:
            return Response(
                {
                    "has_range": False,
                    "range": None,
                    "connection_urls": [],
                    "raes_projection": None,
                    "raes_participant_runtime": None,
                    "lifecycle": None,
                    "vpn_profile_available": False,
                }
            )
        # Use the same domain-owned visibility policy as the legacy context
        # processor so both Mission Control read paths expose identical instances.
        active_range.instances = filter_visible_instances(actor, active_range.instances)
        projection = build_range_raes_projection(active_range.request_id)
        participant_runtime = build_range_participant_runtime_projection(
            active_range.request_id, active_range.instances
        )
        lease = get_mission_control_range_lease(actor)
        return Response(
            {
                "has_range": True,
                "range": active_range.model_dump(mode="json"),
                "connection_urls": build_connection_urls(active_range.instances),
                "raes_projection": projection.to_payload() if projection else None,
                "raes_participant_runtime": participant_runtime.to_payload() if participant_runtime else None,
                "lifecycle": lease.to_payload() if lease else None,
                "vpn_profile_available": has_mission_control_openvpn_profile(actor),
            }
        )


class ExtendRangeLeaseView(MissionControlAPIView):
    """Extend the authenticated actor's Mission Control range by one fixed increment."""

    permission_classes = [
        IsAuthenticatedSessionOrApiToken,
        HasMissionControlActor,
        _range_write_permission(),
        block_participant_lifecycle_permission("extend"),
    ]

    @extend_schema(
        request=None,
        responses={
            200: RangeLeaseResponseSerializer,
            400: ApiErrorSerializer,
            404: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        """Extend only the server-owned lease; caller timestamps are forbidden."""
        if request.body or request.query_params:
            response = self.error_response(
                code="invalid",
                message="Range extension requests must not include a body or query parameters.",
                status_code=400,
            )
        else:
            from cms.services import RangeLeaseConflict, RangeLeaseNotFound

            try:
                lease = cms_extend_mission_control_range(self.actor_user())
            except RangeLeaseNotFound:
                response = self.not_found("Range not found")
            except RangeLeaseConflict:
                response = self.error_response(
                    code="range_extension_unavailable",
                    message="Range cannot be extended.",
                    status_code=409,
                )
            else:
                response = Response({"lifecycle": lease.to_payload()})
        return response


class LaunchRangeView(MissionControlAPIView):
    """Launch a new cyber range."""

    permission_classes = [
        IsAuthenticatedSessionOrApiToken,
        HasMissionControlActor,
        _range_write_permission(),
        block_participant_lifecycle_permission("launch"),
    ]
    # Backpressure (#322): per-actor + fleet admission budget, before CMS.
    throttle_classes = [RangeLaunchRateThrottle]

    # Retry-safe launch (#2086, ADR-063). Caller-supplied idempotency key; bounded
    # so it can never overflow the binding column or become a log/label hazard.
    _RETRY_KEY_HEADER = "Idempotency-Key"
    _MAX_CALLER_KEY_LEN = 200

    @extend_schema(
        request=LaunchRangeSerializer,
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=str,
                location=OpenApiParameter.HEADER,
                required=False,
                description=(
                    "Optional caller retry key (max 200 characters; leading/trailing whitespace "
                    "trimmed, empty treated as absent). When supplied the launch is idempotent: a "
                    "retry with the same key and the same launch selections recovers the original "
                    "range instead of dispatching a duplicate; the same key with different "
                    "selections returns 409."
                ),
            )
        ],
        responses=LaunchRangeResponseSerializer,
        operation_id="api_v1_mission_control_range_launch",
    )
    def post(self, request: Request) -> Response:
        """Validate input and create a range for the authenticated actor."""
        data, error = _validated(self, LaunchRangeSerializer, request.data)
        if error is not None:
            return error
        assert data is not None

        user = self.actor_user()
        caller_key = self._retry_key(request)
        if isinstance(caller_key, Response):
            return caller_key

        # Attempt recovery BEFORE catalog validation so a replay recovers even when
        # the original scenario or agent has since been retired (#2086, ADR-063).
        if caller_key is not None:
            recovered = self._try_recover(user, data, caller_key)
            if recovered is not None:
                return recovered

        return self._launch_range(request, user, data, caller_key)

    def _agents_selection(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize the raw caller agent selection for the retry digest (not catalog-resolved)."""
        if "agents" in data:
            agents = cast(dict[str, int], data["agents"])
            return {"agents": {str(key): int(value) for key, value in sorted(agents.items())}}
        return {"agent_id": data.get("agent_id")}

    def _try_recover(self, user: User, data: dict[str, Any], caller_key: str) -> Response | None:
        """Return a recovered/409 response, or ``None`` for first use (no catalog check)."""
        try:
            outcome = resolve_retry_recovery(
                user,
                scenario=str(data.get("scenario", "basic")),
                agents_selection=self._agents_selection(data),
                workspace_uuid=data.get("workspace_uuid"),
                caller_key=caller_key,
            )
        except RetryKeyConflict:
            logger.info("Retry key conflict: user=%s", user.pk)
            return self.error_response(
                code="retry_key_conflict",
                message="This idempotency key is already bound to a different launch request.",
                status_code=409,
            )
        if outcome is None:
            return None
        return self._bound_range_response(user, outcome, recovered=True)

    def _launch_range(
        self, request: Request, user: User, data: dict[str, Any], caller_key: str | None = None
    ) -> Response:
        """Launch a range once the request body has passed serializer checks."""
        scenario = str(data.get("scenario", "basic"))
        valid_scenarios = {s["id"] for s in cms_list_launchable_scenarios(user, "range_launch")}
        if scenario not in valid_scenarios:
            return self.bad_request("Invalid scenario")

        agents_by_os, agents_error = self._resolve_agents_by_os(user, data)
        if agents_error is not None:
            return agents_error

        return self._create_range(
            request, user, scenario, agents_by_os, data.get("workspace_uuid"), caller_key, self._agents_selection(data)
        )

    def _resolve_agents_by_os(self, user: User, data: dict[str, Any]) -> tuple[dict[str, int] | None, Response | None]:
        """Resolve either the explicit agent map or a legacy single agent id."""
        agents_error: Response | None = None
        agents_by_os: dict[str, int] | None = None
        if "agents" in data:
            agents_by_os = cast(dict[str, int], data["agents"])
        else:
            agent_id = cast(int, data.get("agent_id"))
            try:
                agent = cms_get_agent(user, agent_id)
            except CMSError as exc:
                logger.exception("Agent lookup failed: user=%s agent_id=%s", user.pk, safe_log_value(agent_id))
                agents_error = self.bad_request(classify_user_message(str(exc), default="Agent not available"))
            else:
                os_type = "windows" if agent.os.slug == "windows" else "linux"
                agents_by_os = {os_type: agent_id}
        return agents_by_os, agents_error

    def _retry_key(self, request: Request) -> str | Response | None:
        """Return a bounded caller retry key, ``None`` when absent, or a 400 Response when invalid."""
        raw = request.headers.get(self._RETRY_KEY_HEADER)
        if raw is None:
            return None
        key = raw.strip()
        if not key:
            return None
        if len(key) > self._MAX_CALLER_KEY_LEN:
            return self.bad_request(f"{self._RETRY_KEY_HEADER} must be at most {self._MAX_CALLER_KEY_LEN} characters.")
        return key

    def _create_range(
        self,
        request: Request,
        user: User,
        scenario: str,
        agents_by_os: dict[str, int] | None,
        workspace_uuid: UUID | None = None,
        caller_key: str | None = None,
        agents_selection: dict[str, Any] | None = None,
    ) -> Response:
        """Create a range and record the launch audit event."""
        if caller_key is not None:
            return self._create_range_first_use(
                request, user, scenario, agents_by_os, workspace_uuid, caller_key, agents_selection or {}
            )
        try:
            range_ctx = cms_create_range(user, scenario, agents_by_os or {}, workspace_uuid=workspace_uuid)
        except CMSError as exc:
            return self._launch_failure_response(exc, user, scenario)

        logger.info(
            "Range launched: user=%s request_id=%s agent=%s scenario=%s",
            safe_log_value(user.email),
            range_ctx.request_id,
            safe_log_value(range_ctx.agent_name),
            safe_log_value(scenario),
        )
        _audit_range_lifecycle(
            _raw_request(request),
            AuditAction.PROVISION,
            range_request_id=str(range_ctx.request_id),
            extra_state={"scenario": scenario, "agents": agents_by_os},
        )
        return Response({"success": True, "range": range_ctx.model_dump(mode="json")})

    def _create_range_first_use(
        self,
        request: Request,
        user: User,
        scenario: str,
        agents_by_os: dict[str, int] | None,
        workspace_uuid: UUID | None,
        caller_key: str,
        agents_selection: dict[str, Any],
    ) -> Response:
        """First use of a retry key: dispatch, bind, and audit exactly once (#2086, ADR-063).

        A concurrent contender that wins the key rolls this dispatch back and its
        bound operation is recovered instead (or conflicts, 409). Recovery is not
        reached here (it short-circuits in ``post`` before catalog validation).
        """
        try:
            outcome = bind_first_use_launch(
                user,
                scenario=scenario,
                agents_selection=agents_selection,
                agents_by_os=agents_by_os or {},
                workspace_uuid=workspace_uuid,
                caller_key=caller_key,
            )
        except RetryKeyConflict:
            logger.info("Retry key conflict: user=%s scenario=%s", user.pk, safe_log_value(scenario))
            return self.error_response(
                code="retry_key_conflict",
                message="This idempotency key is already bound to a different launch request.",
                status_code=409,
            )
        except CMSError as exc:
            return self._launch_failure_response(exc, user, scenario)

        if outcome.created:
            logger.info(
                "Range launched (retry-safe): user=%s request_id=%s scenario=%s",
                safe_log_value(user.email),
                outcome.request_id,
                safe_log_value(scenario),
            )
            _audit_range_lifecycle(
                _raw_request(request),
                AuditAction.PROVISION,
                range_request_id=outcome.request_id,
                extra_state={"scenario": scenario, "agents": agents_by_os, "retry_safe": True},
            )
        return self._bound_range_response(user, outcome, recovered=not outcome.created)

    def _bound_range_response(self, user: User, outcome: Any, *, recovered: bool) -> Response:
        """Project the BOUND range (terminal-aware), never the caller's current active range."""
        try:
            range_ctx = get_range_by_request_id(user, outcome.request_id, include_terminal=True)
            range_payload: dict[str, Any] | None = range_ctx.model_dump(mode="json")
        except CMSError:
            range_payload = None
        return Response({"success": True, "recovered": recovered, "range": range_payload})

    def _launch_failure_response(self, exc: CMSError, user: User, scenario: str) -> Response:
        """Map a launch-time CMS failure to its bounded HTTP response.

        Kept distinct from the generic 400: an unavailable workspace scope is an
        opaque 403 (ADR-046-R9) and an enforcing concurrent-range quota is a 409
        Conflict (ADR-046-R10), never a 403 or a request-rate 429.
        """
        if isinstance(exc, WorkspaceLaunchDenied):
            logger.info("Range launch workspace denied: user=%s scenario=%s", user.pk, safe_log_value(scenario))
            return self.error_response(
                code="workspace_not_available",
                message="Selected workspace is not available.",
                status_code=403,
            )
        if isinstance(exc, WorkspaceLaunchQuotaExceeded):
            logger.info("Range launch quota exhausted: user=%s scenario=%s", user.pk, safe_log_value(scenario))
            return self.error_response(
                code="workspace_range_quota_exceeded",
                message="This workspace has reached its concurrent range limit.",
                status_code=409,
            )
        logger.exception("Range creation failed: user=%s scenario=%s", user.pk, safe_log_value(scenario))
        text = str(exc).lower()
        if "already have" in text or "active range" in text:
            response_msg = "You already have an active range"
        else:
            response_msg = classify_user_message(str(exc), default="Range could not be launched")
        return self.bad_request(response_msg)


class RangeLifecycleView(MissionControlAPIView):
    """Base class for range lifecycle mutations."""

    log_verb = ""
    audit_action = ""
    by_request_attr = ""
    by_id_attr = ""
    lifecycle_verb = ""

    def get_permissions(self) -> list[object]:
        """Append the lifecycle-verb participant permission to the base gates."""
        permissions = super().get_permissions()
        permissions.append(block_participant_lifecycle_permission(self.lifecycle_verb)())
        return permissions

    def post(self, request: Request) -> Response:
        """Run the configured range lifecycle service method."""
        import cms.services as cms_services_mod

        data, error = _validated(self, RangeLifecycleSerializer, request.data)
        if error is not None:
            return error
        assert data is not None

        user = self.actor_user()
        request_id = str(data["request_id"]) if data.get("request_id") else None
        range_id = data.get("range_id")
        try:
            if request_id:
                getattr(cms_services_mod, self.by_request_attr)(user, request_id)
                logger.info(
                    "Range %s: user=%s request_id=%s",
                    self.log_verb,
                    safe_log_value(user.email),
                    safe_log_value(request_id),
                )
            else:
                getattr(cms_services_mod, self.by_id_attr)(user, range_id)
                logger.info(
                    "Range %s: user=%s range_id=%s",
                    self.log_verb,
                    safe_log_value(user.email),
                    safe_log_value(range_id),
                )
        except CMSError as exc:
            logger.exception(
                "Range %s failed: user=%s request_id=%s range_id=%s",
                self.log_verb,
                user.pk,
                safe_log_value(request_id),
                safe_log_value(range_id),
            )
            return self.bad_request(classify_user_message(str(exc), default="Range action could not be completed"))

        _audit_range_lifecycle(
            _raw_request(request),
            self.audit_action,
            range_id=range_id,
            range_request_id=request_id,
        )
        return Response({"success": True})


@extend_schema_view(
    post=extend_schema(
        request=RangeLifecycleSerializer,
        responses=SuccessResponseSerializer,
        operation_id="api_v1_mission_control_range_cancel",
    )
)
class CancelRangeView(RangeLifecycleView):
    """Cancel a pending or active range."""

    log_verb = "cancelled"
    audit_action = AuditAction.CANCEL
    by_request_attr = "cancel_range_by_request_id"
    by_id_attr = "cancel_range"
    lifecycle_verb = "cancel"


@extend_schema_view(
    post=extend_schema(
        request=RangeLifecycleSerializer,
        responses=SuccessResponseSerializer,
        operation_id="api_v1_mission_control_range_destroy",
    )
)
class DestroyRangeView(RangeLifecycleView):
    """Destroy a range."""

    log_verb = "destroyed"
    audit_action = AuditAction.DEPROVISION
    by_request_attr = "destroy_range_by_request_id"
    by_id_attr = "destroy_range"
    lifecycle_verb = "destroy"


@extend_schema_view(
    post=extend_schema(
        request=RangeLifecycleSerializer,
        responses=SuccessResponseSerializer,
        operation_id="api_v1_mission_control_range_pause",
    )
)
class PauseRangeView(RangeLifecycleView):
    """Pause a range."""

    log_verb = "paused"
    audit_action = AuditAction.PAUSE
    by_request_attr = "pause_range_by_request_id"
    by_id_attr = "pause_range"
    lifecycle_verb = "pause"


@extend_schema_view(
    post=extend_schema(
        request=RangeLifecycleSerializer,
        responses=SuccessResponseSerializer,
        operation_id="api_v1_mission_control_range_resume",
    )
)
class ResumeRangeView(RangeLifecycleView):
    """Resume a paused range."""

    log_verb = "resumed"
    audit_action = AuditAction.RESUME
    by_request_attr = "resume_range_by_request_id"
    by_id_attr = "resume_range"
    lifecycle_verb = "resume"


class AgentListView(MissionControlReadAPIView):
    """Return the authenticated user's agent list."""

    @extend_schema(responses=AgentListResponseSerializer, operation_id="api_v1_mission_control_agents_list")
    def get(self, request: Request) -> Response:
        """Return agents available to the authenticated actor."""
        return Response(
            {
                "agents": cms_list_agents(self.actor_user()),
                "max_file_size_bytes": cms_max_agent_file_size_bytes(),
            }
        )


class ScenarioListView(MissionControlReadAPIView):
    """Return available range scenarios."""

    @extend_schema(responses=ScenarioListResponseSerializer, operation_id="api_v1_mission_control_scenarios_list")
    def get(self, request: Request) -> Response:
        """Return scenarios available to the authenticated actor."""
        scenarios = cms_list_launchable_scenarios(self.actor_user(), "range_launch")
        return Response({"scenarios": scenarios})


class RangeHistoryView(MissionControlReadAPIView):
    """Return the authenticated user's range history (#1370).

    Backed by ``cms.services.list_mission_control_range_history``, the
    product-scoped history query: it reads through ``all_objects`` so
    soft-deleted terminal ranges (DESTROYED/FAILED, the rows a history view
    exists to show) are INCLUDED, and scopes to
    ``range_source == MISSION_CONTROL`` so CTF-sourced ranges never leak into
    this Mission Control surface. It returns raw ``RangeInstance`` rows
    (newest first), which are projected into ``RangeHistorySerializer``
    explicitly here rather than reusing ``RangePresentationSerializer`` — a
    history row has no hydrated ``instances``/``agent_name``/computed-status
    fields, only the durable identifiers, status, provenance, and timestamps.
    """

    @extend_schema(responses=RangeHistoryResponseSerializer, operation_id="api_v1_mission_control_ranges_list")
    def get(self, request: Request) -> Response:
        """Return the authenticated actor's Mission Control range history, newest first."""
        ranges = list_mission_control_range_history(self.actor_user())
        serializer = RangeHistorySerializer(
            [
                {
                    # ``range_instance.request_id`` is the Django FK shadow
                    # attribute (the related ``Request`` row's integer pk) —
                    # NOT the durable UUID correlation key. That key lives on
                    # the related row as ``Request.request_id``.
                    "request_id": range_instance.request.request_id if range_instance.request else None,
                    "range_id": range_instance.range_id,
                    "scenario_id": range_instance.scenario_id,
                    "status": range_instance.status,
                    "range_source": range_instance.range_source,
                    "created_at": range_instance.created_at,
                    "updated_at": range_instance.updated_at,
                    "deleted_at": range_instance.deleted_at,
                }
                for range_instance in ranges
            ],
            many=True,
        )
        return Response({"ranges": serializer.data})
