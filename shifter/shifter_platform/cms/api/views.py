"""DRF views for the canonical CMS API."""

from __future__ import annotations

import logging

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.api.permissions import CMS_READ_PERMISSIONS, CMS_WRITE_PERMISSIONS, cms_actor_user
from cms.api.serializers import YAMLContentSerializer
from cms.scenario_editor import services as scenario_services
from shared.api.errors import api_error_response

logger = logging.getLogger(__name__)


def _actor_user(request: Request) -> User:
    """Return the CMS actor after permissions have admitted the request."""
    user = cms_actor_user(request)
    if user is None:
        raise AssertionError("CMS actor unavailable after permission check")
    return user


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
