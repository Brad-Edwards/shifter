"""DRF views for Mission Control JSON endpoints."""

from __future__ import annotations

import logging
import os
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.services import (
    ScriptUploadError,
    complete_script_upload,
    initiate_script_upload,
    list_scripts,
)
from cms.services import (
    cancel_upload as cms_cancel_upload,
)
from cms.services import (
    complete_upload as cms_complete_upload,
)
from cms.services import (
    delete_credential as cms_delete_credential,
)
from cms.services import (
    initiate_upload as cms_initiate_upload,
)
from cms.services import (
    list_ngfws as cms_list_ngfws,
)
from mission_control.api.authentication import CsrfExemptSessionAuthentication
from mission_control.api.permissions import (
    HasMissionControlActor,
    block_participant_lifecycle_permission,
    mission_control_actor_user,
)
from mission_control.api.serializers import (
    CredentialCreateSerializer,
    GuacamoleInstanceSerializer,
    LaunchRangeSerializer,
    NGFWCreateSerializer,
    NGFWDestroySerializer,
    RangeLifecycleSerializer,
    ScriptUploadSerializer,
    UploadCancelSerializer,
    UploadCompleteSerializer,
    UploadInitiateSerializer,
)
from mission_control.guacamole_bootstrap import consume_ready_url
from mission_control.models import GuacamoleBootstrapRequest
from mission_control.upload_session import check_upload_in_progress, set_upload_in_progress
from mission_control.utils import build_connection_urls
from mission_control.views._common import (
    _audit_range_lifecycle,
    _logger,
    _pkg,
)
from mission_control.views._credentials import _CredentialError, _persist_credential, _validate_credential_spec
from mission_control.views._guacamole import (
    _get_guac_settings,
    _resolve_and_build_ngfw_ssh_url,
    _resolve_and_build_range_ssh_url,
    _resolve_and_build_rdp_url,
    _wrap_bootstrap_error,
)
from mission_control.views._guacamole_bootstrap import (
    _authenticated_user_id,
    _BootstrapViewError,
    _mark_expired,
)
from mission_control.views._guacamole_bootstrap import (
    guacamole_bootstrap_response as _guacamole_bootstrap_response,
)
from mission_control.views._ngfw import _extract_ngfw_create_payload, _NgfwError, _run_ngfw_destroy
from risk_register.models import AuditLog
from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSessionOrApiToken
from shared.api_tokens import scopes
from shared.api_tokens.authentication import ApiTokenAuthentication
from shared.api_tokens.permissions import require_scope
from shared.errors import classify_user_message
from shared.exceptions import CMSError
from shared.log_sanitize import safe_log_value

logger = logging.getLogger(__name__)


def _scope_permission(read_scope: str, write_scope: str | None = None) -> type:
    return require_scope(read_scope, write_scope or read_scope)


def _range_read_permission() -> type:
    return require_scope(scopes.MISSION_CONTROL_RANGE_READ, scopes.MISSION_CONTROL_RANGE_READ)


def _range_write_permission() -> type:
    return require_scope(scopes.MISSION_CONTROL_RANGE_READ, scopes.MISSION_CONTROL_RANGE_WRITE)


def _upload_write_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_UPLOAD_WRITE)


def _guacamole_read_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_GUACAMOLE_READ)


def _ngfw_read_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_NGFW_READ)


def _ngfw_write_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_NGFW_READ, scopes.MISSION_CONTROL_NGFW_WRITE)


def _credentials_write_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_CREDENTIALS_WRITE)


def _script_read_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_SCRIPT_READ)


def _script_write_permission() -> type:
    return _scope_permission(scopes.MISSION_CONTROL_SCRIPT_READ, scopes.MISSION_CONTROL_SCRIPT_WRITE)


class MissionControlAPIView(APIView):
    """Base class for authenticated Mission Control DRF endpoints."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _range_write_permission()]

    def determine_version(self, request, *args, **kwargs):
        if _is_legacy_request(request):
            return "v1", None
        return super().determine_version(request, *args, **kwargs)

    def handle_exception(self, exc):
        if _is_legacy_request(self.request):
            if isinstance(exc, ParseError):
                return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)
            if isinstance(exc, DRFPermissionDenied) and str(getattr(exc, "detail", "")) == "Forbidden":
                return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        response = super().handle_exception(exc)
        if _is_legacy_request(self.request) and response is not None:
            response.data = {"error": _legacy_error_message(response.data)}
        return response

    def actor_user(self):
        user = mission_control_actor_user(self.request)
        if user is None:
            raise DjangoPermissionDenied("Authenticated user unavailable")
        return user

    def invalid(self, serializer) -> Response:
        if _is_legacy_request(self.request):
            return Response(
                {"error": _first_serializer_error(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return api_error_response(
            code="invalid",
            message="Invalid request",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=serializer.errors,
            request=self.request,
        )

    def bad_request(self, message: str) -> Response:
        if _is_legacy_request(self.request):
            return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)
        return api_error_response(
            code="bad_request",
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            request=self.request,
        )

    def not_found(self, message: str) -> Response:
        if _is_legacy_request(self.request):
            return Response({"error": message}, status=status.HTTP_404_NOT_FOUND)
        return api_error_response(
            code="not_found",
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            request=self.request,
        )

    def error_response(self, *, code: str, message: str, status_code: int) -> Response:
        if _is_legacy_request(self.request):
            return Response({"error": message}, status=status_code)
        return api_error_response(code=code, message=message, status_code=status_code, request=self.request)


class MissionControlReadAPIView(MissionControlAPIView):
    """Read-only Mission Control endpoint."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _range_read_permission()]


def _raw_request(drf_request):
    raw = getattr(drf_request, "_request", drf_request)
    raw.auth = getattr(drf_request, "auth", None)
    return raw


def _is_legacy_request(request) -> bool:
    return str(getattr(request, "path", "")).startswith("/mission-control/")


def _first_serializer_error(errors: object) -> str:
    if isinstance(errors, dict):
        for value in errors.values():
            return _first_serializer_error(value)
    if isinstance(errors, list) and errors:
        return _first_serializer_error(errors[0])
    if errors:
        return str(errors)
    return "Invalid request"


def _legacy_error_message(data: object) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            value = error.get("message") or error.get("detail") or error.get("code")
            return str(value or "Request could not be processed")
        if error is not None:
            return str(error)
        detail = data.get("detail")
        if detail is not None:
            return str(detail)
    return "Request could not be processed"


def _validated(view: MissionControlAPIView, serializer_class, data: object):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        return None, view.invalid(serializer)
    return serializer.validated_data, None


def _is_empty_legacy_body(request) -> bool:
    if not _is_legacy_request(request):
        return False
    raw = getattr(request, "_request", request)
    return getattr(raw, "body", b"") == b""


def _guacamole_bootstrap_url_names(request) -> dict[str, str]:
    match = getattr(request, "resolver_match", None)
    if getattr(match, "namespace", "") == "v1:mission_control":
        return {
            "status_url_name": "v1:mission_control:guacamole-bootstrap-status",
            "open_url_name": "v1:mission_control:guacamole-bootstrap-open",
        }
    return {}


class CurrentRangeView(MissionControlReadAPIView):
    """Return the current user's active range."""

    def get(self, request) -> Response:
        active_range = _pkg().get_active_range(self.actor_user())
        if not active_range:
            return Response({"has_range": False, "range": None, "connection_urls": []})
        return Response(
            {
                "has_range": True,
                "range": active_range.model_dump(mode="json"),
                "connection_urls": build_connection_urls(active_range.instances),
            }
        )


class LaunchRangeView(MissionControlAPIView):
    """Launch a new cyber range."""

    permission_classes = [
        IsAuthenticatedSessionOrApiToken,
        HasMissionControlActor,
        _range_write_permission(),
        block_participant_lifecycle_permission("launch"),
    ]

    def post(self, request) -> Response:
        if _is_empty_legacy_body(request):
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

        data, error = _validated(self, LaunchRangeSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        scenario = data.get("scenario", "basic")
        valid_scenarios = {s["id"] for s in _pkg().cms_list_scenarios(user)}
        if scenario not in valid_scenarios:
            return self.bad_request("Invalid scenario")

        agents_error: Response | None = None
        agents_by_os: dict[str, int] | None = None
        if "agents" in data:
            agents_by_os = data["agents"]
        else:
            agent_id = data.get("agent_id")
            try:
                agent = _pkg().cms_get_agent(user, agent_id)
            except CMSError as exc:
                _logger().exception("Agent lookup failed: user=%s agent_id=%s", user.pk, safe_log_value(agent_id))
                agents_error = self.bad_request(classify_user_message(str(exc), default="Agent not available"))
            else:
                os_type = "windows" if agent.os.slug == "windows" else "linux"
                agents_by_os = {os_type: agent_id}
        if agents_error is not None:
            return agents_error

        try:
            range_ctx = _pkg().cms_create_range(user, scenario, agents_by_os or {})
        except CMSError as exc:
            _logger().exception("Range creation failed: user=%s scenario=%s", user.pk, safe_log_value(scenario))
            text = str(exc).lower()
            if "already have" in text or "active range" in text:
                response_msg = "You already have an active range"
            else:
                response_msg = classify_user_message(str(exc), default="Range could not be launched")
            return self.bad_request(response_msg)

        _logger().info(
            "Range launched: user=%s request_id=%s agent=%s scenario=%s",
            safe_log_value(user.email),
            range_ctx.request_id,
            safe_log_value(range_ctx.agent_name),
            safe_log_value(scenario),
        )
        _audit_range_lifecycle(
            _raw_request(request),
            AuditLog.Action.PROVISION,
            range_request_id=str(range_ctx.request_id),
            extra_state={"scenario": scenario, "agents": agents_by_os},
        )
        return Response({"success": True, "range": range_ctx.model_dump(mode="json")})


class RangeLifecycleView(MissionControlAPIView):
    """Base class for range lifecycle mutations."""

    log_verb = ""
    audit_action = ""
    by_request_attr = ""
    by_id_attr = ""
    lifecycle_verb = ""

    def get_permissions(self):
        permissions = super().get_permissions()
        permissions.append(block_participant_lifecycle_permission(self.lifecycle_verb)())
        return permissions

    def post(self, request) -> Response:
        import cms.services as cms_services_mod

        data, error = _validated(self, RangeLifecycleSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        request_id = str(data["request_id"]) if data.get("request_id") else None
        range_id = data.get("range_id")
        try:
            if request_id:
                getattr(cms_services_mod, self.by_request_attr)(user, request_id)
                _logger().info(
                    "Range %s: user=%s request_id=%s",
                    self.log_verb,
                    safe_log_value(user.email),
                    safe_log_value(request_id),
                )
            else:
                getattr(cms_services_mod, self.by_id_attr)(user, range_id)
                _logger().info(
                    "Range %s: user=%s range_id=%s",
                    self.log_verb,
                    safe_log_value(user.email),
                    safe_log_value(range_id),
                )
        except CMSError as exc:
            _logger().exception(
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


class CancelRangeView(RangeLifecycleView):
    log_verb = "cancelled"
    audit_action = AuditLog.Action.CANCEL
    by_request_attr = "cancel_range_by_request_id"
    by_id_attr = "cancel_range"
    lifecycle_verb = "cancel"


class DestroyRangeView(RangeLifecycleView):
    log_verb = "destroyed"
    audit_action = AuditLog.Action.DEPROVISION
    by_request_attr = "destroy_range_by_request_id"
    by_id_attr = "destroy_range"
    lifecycle_verb = "destroy"


class PauseRangeView(RangeLifecycleView):
    log_verb = "paused"
    audit_action = AuditLog.Action.PAUSE
    by_request_attr = "pause_range_by_request_id"
    by_id_attr = "pause_range"
    lifecycle_verb = "pause"


class ResumeRangeView(RangeLifecycleView):
    log_verb = "resumed"
    audit_action = AuditLog.Action.RESUME
    by_request_attr = "resume_range_by_request_id"
    by_id_attr = "resume_range"
    lifecycle_verb = "resume"


class AgentListView(MissionControlReadAPIView):
    """Return the authenticated user's agent list."""

    def get(self, request) -> Response:
        return Response({"agents": _pkg().cms_list_agents(self.actor_user())})


class ScenarioListView(MissionControlReadAPIView):
    """Return available range scenarios."""

    def get(self, request) -> Response:
        scenarios: list[dict[str, Any]] = _pkg().cms_list_scenarios(self.actor_user())
        return Response({"scenarios": scenarios})


class UploadInitiateView(MissionControlAPIView):
    """Initiate a presigned agent upload."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _upload_write_permission()]

    def post(self, request) -> Response:
        if check_upload_in_progress(request.session):
            return self.error_response(
                code="conflict",
                message="An upload is already in progress. Please wait for it to complete.",
                status_code=status.HTTP_409_CONFLICT,
            )

        data, error = _validated(self, UploadInitiateSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        filename = os.path.basename(data["filename"])
        try:
            result = cms_initiate_upload(user, data["name"], filename, data["file_size"], data["agent_type"])
        except CMSError as exc:
            logger.exception(
                "Upload initiation failed: user=%s filename=%s",
                safe_log_value(user.email),
                safe_log_value(filename),
            )
            return self.bad_request(classify_user_message(str(exc), default="Upload could not be initiated"))

        set_upload_in_progress(request.session, True)
        safe_filename = filename.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:200]
        safe_email = user.email.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:200]
        logger.info("Upload initiated: user=%s filename=%s size=%d", safe_email, safe_filename, data["file_size"])
        return Response(result)


class UploadCompleteView(MissionControlAPIView):
    """Complete a presigned agent upload."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _upload_write_permission()]

    def post(self, request) -> Response:
        data, error = _validated(self, UploadCompleteSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        try:
            agent = cms_complete_upload(user, data.get("upload_token", ""))
        except CMSError as exc:
            set_upload_in_progress(request.session, False)
            logger.exception("Upload completion failed: user=%s", user.pk)
            return self.bad_request(classify_user_message(str(exc), default="Upload could not be completed"))

        set_upload_in_progress(request.session, False)
        logger.info("Upload completed: user=%s agent_id=%s", safe_log_value(user.email), agent.id)
        return Response(
            {
                "success": True,
                "agent_id": agent.id,
                "message": f"Agent '{agent.name}' uploaded successfully.",
            }
        )


class UploadCancelView(MissionControlAPIView):
    """Cancel an in-progress agent upload."""

    authentication_classes = [ApiTokenAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _upload_write_permission()]

    def post(self, request) -> Response:
        try:
            raw_data = request.data
        except ParseError:
            raw_data = {}
        data, error = _validated(self, UploadCancelSerializer, raw_data)
        if error is not None:
            return error

        upload_token = data.get("upload_token", "")
        user = self.actor_user()
        if upload_token:
            try:
                cms_cancel_upload(user, upload_token)
                logger.info("Cancelled upload cleaned up: user=%s", safe_log_value(user.email))
            except CMSError:
                pass

        set_upload_in_progress(request.session, False)
        return Response({"success": True})


class GuacamoleRDPURLView(MissionControlReadAPIView):
    """Queue Guacamole URL bootstrap for range RDP access."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _guacamole_read_permission()]

    def post(self, request) -> JsonResponse | Response:
        data, error = _validated(self, GuacamoleInstanceSerializer, request.data)
        if error is not None:
            return error
        user = self.actor_user()
        try:
            guac_settings = _get_guac_settings("RDP")
        except Exception as exc:
            if hasattr(exc, "response"):
                return exc.response
            raise
        return _guacamole_bootstrap_response(
            user=user,
            protocol=GuacamoleBootstrapRequest.Protocol.RDP,
            target_id=data["instance_uuid"],
            **_guacamole_bootstrap_url_names(request),
            build_url=lambda: _wrap_bootstrap_error(
                "RDP",
                lambda: _resolve_and_build_rdp_url(
                    user=user,
                    instance_uuid=data["instance_uuid"],
                    guac_settings=guac_settings,
                ),
            ),
        )


class GuacamoleRangeSSHURLView(MissionControlReadAPIView):
    """Queue Guacamole URL bootstrap for range SSH access."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _guacamole_read_permission()]

    def post(self, request) -> JsonResponse | Response:
        data, error = _validated(self, GuacamoleInstanceSerializer, request.data)
        if error is not None:
            return error
        user = self.actor_user()
        try:
            guac_settings = _get_guac_settings("SSH")
        except Exception as exc:
            if hasattr(exc, "response"):
                return exc.response
            raise
        return _guacamole_bootstrap_response(
            user=user,
            protocol=GuacamoleBootstrapRequest.Protocol.RANGE_SSH,
            target_id=data["instance_uuid"],
            **_guacamole_bootstrap_url_names(request),
            build_url=lambda: _wrap_bootstrap_error(
                "SSH",
                lambda: _resolve_and_build_range_ssh_url(
                    user=user,
                    instance_uuid=data["instance_uuid"],
                    guac_settings=guac_settings,
                ),
            ),
        )


class GuacamoleNGFWSSHURLView(MissionControlReadAPIView):
    """Queue Guacamole URL bootstrap for NGFW SSH access."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _guacamole_read_permission()]

    def post(self, request, app_id: str) -> JsonResponse:
        user = self.actor_user()
        try:
            guac_settings = _get_guac_settings("SSH")
        except Exception as exc:
            if hasattr(exc, "response"):
                return exc.response
            raise
        logger.info(
            "Guacamole SSH bootstrap queued for NGFW: user=%s ngfw_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(app_id),
        )
        return _guacamole_bootstrap_response(
            user=user,
            protocol=GuacamoleBootstrapRequest.Protocol.NGFW_SSH,
            target_id=str(app_id),
            **_guacamole_bootstrap_url_names(request),
            build_url=lambda: _wrap_bootstrap_error(
                "SSH",
                lambda: _resolve_and_build_ngfw_ssh_url(user=user, app_id=app_id, guac_settings=guac_settings),
            ),
        )


class GuacamoleBootstrapStatusView(MissionControlReadAPIView):
    """Return Guacamole bootstrap status."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _guacamole_read_permission()]

    def get(self, request, request_id) -> JsonResponse:
        user = self.actor_user()
        try:
            bootstrap = GuacamoleBootstrapRequest.objects.get(pk=request_id, user_id=_authenticated_user_id(user))
        except GuacamoleBootstrapRequest.DoesNotExist:
            return JsonResponse({"error": "Guacamole bootstrap request not found"}, status=404)
        except _BootstrapViewError as err:
            return err.response
        return _status_response(bootstrap)


class GuacamoleBootstrapOpenView(MissionControlReadAPIView):
    """Render the compatibility opener for a Guacamole bootstrap."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _guacamole_read_permission()]

    def get(self, request, request_id) -> HttpResponse:
        user = self.actor_user()
        try:
            GuacamoleBootstrapRequest.objects.only("id").get(pk=request_id, user_id=_authenticated_user_id(user))
        except GuacamoleBootstrapRequest.DoesNotExist:
            return HttpResponse("Guacamole session request not found.", status=404, content_type="text/plain")
        except _BootstrapViewError as err:
            return err.response

        status_url_name = _guacamole_bootstrap_url_names(request).get(
            "status_url_name",
            "mission_control:guacamole_bootstrap_status",
        )
        status_url = reverse(status_url_name, kwargs={"request_id": request_id})
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Opening session</title>
</head>
<body>
  <p id="status">Opening session...</p>
  <script>
    const statusUrl = {status_url!r};
    const statusEl = document.getElementById('status');
    let attempts = 0;
    async function poll() {{
      attempts += 1;
      const response = await fetch(statusUrl, {{ headers: {{ 'Accept': 'application/json' }} }});
      const data = await response.json().catch(() => ({{}}));
      if (!response.ok) {{
        statusEl.textContent = data.error || 'Failed to open session.';
        return;
      }}
      if (data.url) {{
        globalThis.location.replace(data.url);
        return;
      }}
      if (attempts >= 60) {{
        statusEl.textContent = 'Session request timed out.';
        return;
      }}
      setTimeout(poll, 1000);
    }}
    poll().catch(() => {{
      statusEl.textContent = 'Failed to open session.';
    }});
  </script>
</body>
</html>"""
        return HttpResponse(html)


def _status_response(bootstrap: GuacamoleBootstrapRequest) -> JsonResponse:
    payload: dict[str, str | int] = {"request_id": str(bootstrap.id), "status": bootstrap.status}
    status_code = 200
    retry_after = False

    if bootstrap.duration_ms is not None:
        payload["duration_ms"] = bootstrap.duration_ms

    if bootstrap.is_expired:
        _mark_expired(bootstrap)
        _clear_parked_url(bootstrap)
        payload["status"] = bootstrap.status
        payload["error"] = bootstrap.error_message or "Guacamole session request expired"
        status_code = 410
    elif bootstrap.status == GuacamoleBootstrapRequest.Status.SUCCEEDED:
        url = consume_ready_url(request_id=bootstrap.id, user_id=bootstrap.user_id)
        if url:
            payload["url"] = url
        else:
            payload["error"] = "Guacamole session link is no longer available"
            status_code = 410
    elif bootstrap.status == GuacamoleBootstrapRequest.Status.FAILED:
        payload["error"] = bootstrap.error_message or "Guacamole session bootstrap failed"
        status_code = bootstrap.error_status_code
    else:
        retry_after = True

    response = JsonResponse(payload, status=status_code)
    if retry_after:
        response["Retry-After"] = "1"
    return response


def _clear_parked_url(bootstrap: GuacamoleBootstrapRequest) -> None:
    if bootstrap.result_url:
        bootstrap.result_url = ""
        bootstrap.save(update_fields=("result_url", "updated_at"))


class NGFWCreateView(MissionControlAPIView):
    """Create a new NGFW."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _ngfw_write_permission()]

    def post(self, request) -> Response | JsonResponse:
        data, error = _validated(self, NGFWCreateSerializer, request.data)
        if error is not None:
            return error
        user = self.actor_user()
        payload = _extract_ngfw_create_payload(data)
        try:
            ngfw_ref = _pkg().cms_create_ngfw(user=user, **payload)
        except (TypeError, ValueError, CMSError) as exc:
            logger.exception("NGFW creation failed: user=%s name=%s", user.pk, safe_log_value(payload.get("name", "")))
            return self.bad_request(classify_user_message(str(exc), default="NGFW could not be created"))

        logger.info("NGFW provisioning started: user=%s app_id=%s", safe_log_value(user.email), ngfw_ref.app_id)
        return Response({"id": str(ngfw_ref.app_id), "name": payload["name"], "status": "provisioning"}, status=201)


class NGFWListView(MissionControlReadAPIView):
    """List user's NGFWs."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _ngfw_read_permission()]

    def get(self, request) -> Response:
        ngfws = cms_list_ngfws(self.actor_user())
        return Response(
            {
                "ngfws": [
                    {
                        "id": str(n.app_id),
                        "name": n.name,
                        "status": n.status,
                        "created_at": n.created_at.isoformat(),
                        "serial_number": n.serial_number,
                    }
                    for n in ngfws
                ]
            }
        )


class NGFWDestroyView(MissionControlAPIView):
    """Destroy an NGFW."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _ngfw_write_permission()]

    def post(self, request, app_id: str) -> Response | JsonResponse:
        data, error = _validated(self, NGFWDestroySerializer, request.data)
        if error is not None:
            return error
        user = self.actor_user()
        try:
            _run_ngfw_destroy(user, app_id, data.get("confirm_name", ""))
        except Http404:
            raise
        except _NgfwError as err:
            return err.response

        logger.info(
            "NGFW deprovisioning started: user=%s app_id=%s",
            safe_log_value(user.email),
            safe_log_value(app_id),
        )
        return Response({"status": "deprovisioning"})


class CredentialCreateView(MissionControlAPIView):
    """Create a credential."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _credentials_write_permission()]

    def post(self, request) -> Response | JsonResponse:
        data, error = _validated(self, CredentialCreateSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        credential_type_slug = data["credential_type"]
        payload = dict(data)
        payload["user_id"] = user.id
        if payload.get("expires_at") == "":
            payload["expires_at"] = None
        try:
            spec = _validate_credential_spec(payload, credential_type_slug)
            kwargs = spec.model_dump(exclude={"user_id"})
            cred_ref = _persist_credential(user, credential_type_slug, kwargs)
        except _CredentialError as err:
            return err.response

        logger.info(
            "Credential created: user=%s credential_id=%s type=%s",
            safe_log_value(user.email),
            cred_ref.credential_id,
            safe_log_value(credential_type_slug),
        )
        return Response({"id": cred_ref.credential_id, "name": spec.name, "credential_type": credential_type_slug}, 201)


class CredentialDeleteView(MissionControlAPIView):
    """Soft-delete a credential."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _credentials_write_permission()]

    def post(self, request, credential_id: int) -> Response:
        user = self.actor_user()
        try:
            cms_delete_credential(user, credential_id)
        except CMSError:
            raise Http404("Credential not found") from None

        logger.info(
            "Credential deleted: user=%s credential_id=%s",
            safe_log_value(user.email),
            safe_log_value(credential_id),
        )
        return Response({"success": True})


class ScriptListView(MissionControlReadAPIView):
    """List experiment scripts for the authenticated user."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _script_read_permission()]

    def get(self, request) -> Response:
        scripts = list_scripts(self.actor_user())
        return Response({"scripts": [{"id": s.pk, "name": s.name, "filename": s.original_filename} for s in scripts]})


class ScriptUploadView(MissionControlAPIView):
    """Initiate or complete an experiment-script upload."""

    permission_classes = [IsAuthenticatedSessionOrApiToken, HasMissionControlActor, _script_write_permission()]

    def post(self, request) -> Response:
        data, error = _validated(self, ScriptUploadSerializer, request.data)
        if error is not None:
            return error

        user = self.actor_user()
        upload_token = data.get("upload_token")
        if upload_token:
            return self._complete_script(user, upload_token)
        return self._initiate_script(user, data)

    def _complete_script(self, user, upload_token: str) -> Response:
        try:
            script = complete_script_upload(user, upload_token)
        except ScriptUploadError as exc:
            logger.exception("Script upload completion failed: user=%s", user.pk)
            return self.bad_request(classify_user_message(str(exc), default="Upload could not be completed"))

        logger.info("Script upload completed: user=%s script_id=%s", safe_log_value(user.email), script.pk)
        return Response(
            {"success": True, "script_id": script.pk, "message": f"Script '{script.name}' uploaded successfully."}
        )

    def _initiate_script(self, user, data: dict[str, Any]) -> Response:
        filename = os.path.basename(data["filename"])
        try:
            result = initiate_script_upload(user, data["name"], filename, data["file_size"])
        except ScriptUploadError as exc:
            logger.exception("Script upload initiation failed: user=%s", user.pk)
            return self.bad_request(classify_user_message(str(exc), default="Upload could not be initiated"))

        safe_filename = filename.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:200]
        safe_email = user.email.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:200]
        logger.info(
            "Script upload initiated: user=%s filename=%s size=%d",
            safe_email,
            safe_filename,
            data["file_size"],
        )
        return Response(result)


# Legacy ``mission_control.views`` export names. These remain callables so
# existing direct imports and URL names keep working while the implementation is
# DRF underneath.
get_range = CurrentRangeView.as_view()
launch_range = LaunchRangeView.as_view()
cancel_range = CancelRangeView.as_view()
destroy_range = DestroyRangeView.as_view()
pause_range = PauseRangeView.as_view()
resume_range = ResumeRangeView.as_view()
list_agents = AgentListView.as_view()
list_scenarios = ScenarioListView.as_view()
initiate_upload = UploadInitiateView.as_view()
complete_upload = UploadCompleteView.as_view()
cancel_upload = UploadCancelView.as_view()
guacamole_rdp_url = GuacamoleRDPURLView.as_view()
guacamole_ssh_url = GuacamoleRangeSSHURLView.as_view()
api_ngfw_ssh_url = GuacamoleNGFWSSHURLView.as_view()
guacamole_bootstrap_status = GuacamoleBootstrapStatusView.as_view()
guacamole_bootstrap_open = GuacamoleBootstrapOpenView.as_view()
api_ngfw_create = NGFWCreateView.as_view()
api_ngfw_list = NGFWListView.as_view()
api_ngfw_destroy = NGFWDestroyView.as_view()
api_credential_create = CredentialCreateView.as_view()
api_credential_delete = CredentialDeleteView.as_view()
api_list_scripts = ScriptListView.as_view()
file_upload = ScriptUploadView.as_view()
