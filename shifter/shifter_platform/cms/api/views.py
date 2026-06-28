"""DRF views for the canonical CMS API."""

from __future__ import annotations

import logging
from typing import cast

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.api.permissions import CMS_READ_PERMISSIONS, CMS_WRITE_PERMISSIONS, cms_actor_user
from cms.api.serializers import ScriptUploadCompleteSerializer, ScriptUploadInitiateSerializer, YAMLContentSerializer
from cms.experiments import services as experiment_services
from cms.experiments.exceptions import ExperimentValidationError, ScriptUploadError
from cms.scenario_editor import services as scenario_services
from shared.api.errors import api_error_response
from shared.errors import classify_user_message
from shared.log_sanitize import safe_log_value

logger = logging.getLogger(__name__)


def _actor_user(request: Request) -> User:
    """Return the CMS actor after permissions have admitted the request."""
    user = cms_actor_user(request)
    if user is None:
        raise AssertionError("CMS actor unavailable after permission check")
    return user


class ScenarioInstancesView(APIView):
    """Return scenario instance metadata for experiment authoring."""

    permission_classes = CMS_READ_PERMISSIONS

    def get(self, request: Request, scenario_id: str) -> Response:
        """Return the instance list for a scenario template."""
        user = _actor_user(request)
        logger.info(
            "cms_api_scenario_instances: user_id=%s scenario_id=%s",
            user.pk,
            safe_log_value(scenario_id),
        )
        try:
            instances = experiment_services.get_scenario_instances(scenario_id, user=user)
        except ExperimentValidationError as exc:
            logger.exception(
                "cms_api_scenario_instances: validation error scenario_id=%s",
                safe_log_value(scenario_id),
            )
            return api_error_response(
                code="invalid",
                message=classify_user_message(str(exc), default="Invalid scenario request"),
                status_code=status.HTTP_400_BAD_REQUEST,
                request=request,
            )
        except Exception:
            logger.exception("cms_api_scenario_instances: unexpected error")
            return api_error_response(
                code="server_error",
                message="An unexpected error occurred.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                request=request,
            )
        return Response({"instances": instances})


class YAMLValidateView(APIView):
    """Validate scenario YAML without saving it."""

    permission_classes = CMS_READ_PERMISSIONS

    def post(self, request: Request) -> Response:
        """Return a domain validation result for YAML editor callers."""
        serializer = YAMLContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parsed, errors = scenario_services.validate_yaml(serializer.validated_data["yaml_content"])
        if errors:
            return Response({"valid": False, "errors": errors, "definition": None})
        return Response({"valid": True, "errors": [], "definition": parsed})


class YAMLScenarioCreateView(APIView):
    """Create a custom scenario from YAML content."""

    permission_classes = CMS_WRITE_PERMISSIONS

    def post(self, request: Request) -> Response:
        """Create a custom scenario through the scenario-editor service layer."""
        serializer = YAMLContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _actor_user(request)
        fields, errors = scenario_services.create_scenario_from_yaml_post(
            user, serializer.validated_data["yaml_content"]
        )
        if errors or fields is None:
            return api_error_response(
                code="invalid",
                message="Invalid scenario YAML",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"errors": errors},
                request=request,
            )
        return Response({"scenario_id": fields.scenario_id, "name": fields.name}, status=status.HTTP_201_CREATED)


class ScriptUploadInitiateView(APIView):
    """Initiate a presigned experiment-script upload."""

    permission_classes = CMS_WRITE_PERMISSIONS

    def post(self, request: Request) -> Response:
        """Validate upload initiation input and delegate to CMS services."""
        serializer = ScriptUploadInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _actor_user(request)
        data = serializer.validated_data
        filename = cast(str, data["filename"])
        try:
            result = experiment_services.initiate_script_upload(
                user,
                cast(str, data["name"]),
                filename,
                cast(int, data["file_size"]),
            )
        except ScriptUploadError as exc:
            logger.exception(
                "cms_api_script_upload_initiate: failed user_id=%s filename=%s",
                user.pk,
                safe_log_value(filename),
            )
            return api_error_response(
                code="invalid",
                message=classify_user_message(str(exc), default="Upload could not be initiated"),
                status_code=status.HTTP_400_BAD_REQUEST,
                request=request,
            )
        return Response(result)


class ScriptUploadCompleteView(APIView):
    """Complete a presigned experiment-script upload."""

    permission_classes = CMS_WRITE_PERMISSIONS

    def post(self, request: Request) -> Response:
        """Finalize a script upload through CMS services."""
        serializer = ScriptUploadCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _actor_user(request)
        try:
            script = experiment_services.complete_script_upload(user, serializer.validated_data["upload_token"])
        except ScriptUploadError as exc:
            logger.exception("cms_api_script_upload_complete: failed user_id=%s", user.pk)
            return api_error_response(
                code="invalid",
                message=classify_user_message(str(exc), default="Upload could not be completed"),
                status_code=status.HTTP_400_BAD_REQUEST,
                request=request,
            )
        return Response(
            {
                "script": {
                    "id": script.pk,
                    "name": script.name,
                    "original_filename": script.original_filename,
                    "file_size_bytes": script.file_size_bytes,
                }
            }
        )
