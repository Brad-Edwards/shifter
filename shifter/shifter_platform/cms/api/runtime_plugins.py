"""Session-authenticated tenant plugin administration."""

from dataclasses import asdict
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.services import change_runtime_plugin, install_runtime_plugin, list_runtime_plugins
from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSession
from shared.exceptions import ValidationError
from workspaces.services import OrganizationAuthorizationError

from .preparation_adapters import PreparationSerializer, preparation_request_audit


class RegistryCredentialsSerializer(PreparationSerializer):
    username = serializers.CharField(max_length=8192, trim_whitespace=False)
    password = serializers.CharField(max_length=8192, trim_whitespace=False, write_only=True)


class RuntimePluginInstallSerializer(PreparationSerializer):
    manifest = serializers.JSONField()
    registry_credentials = RegistryCredentialsSerializer(required=False, write_only=True)


class RuntimePluginActionSerializer(PreparationSerializer):
    action = serializers.ChoiceField(choices=["disable", "enable", "retry", "retire"])
    registry_credentials = RegistryCredentialsSerializer(required=False, write_only=True)


class RuntimePluginViewSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    organization_uuid = serializers.UUIDField()
    manifest = serializers.JSONField()
    manifest_digest = serializers.CharField()
    state = serializers.ChoiceField(choices=["checking", "ready", "failed", "disabled", "retired"])
    failure_code = serializers.CharField(allow_blank=True)
    has_registry_credentials = serializers.BooleanField()


class RuntimePluginPageSerializer(serializers.Serializer):
    results = RuntimePluginViewSerializer(many=True)
    next_cursor = serializers.UUIDField(allow_null=True)


def _error(request: Request, exc: Exception) -> Response:
    denied = isinstance(exc, OrganizationAuthorizationError)
    return api_error_response(
        code="plugin_access_denied" if denied else "plugin_invalid",
        message="Organization access denied" if denied else exc.message,
        status_code=403 if denied else 400,
        request=request,
    )


class RuntimePluginListCreateView(APIView):
    """Organization administrators install their own plugins without staff status."""

    permission_classes = [IsAuthenticatedSession]

    @extend_schema(
        parameters=[OpenApiParameter("cursor", type=UUID)],
        responses=RuntimePluginPageSerializer,
    )
    def get(self, request: Request, organization_uuid: UUID) -> Response:
        try:
            return Response(
                asdict(
                    list_runtime_plugins(
                        request.user,
                        organization_uuid,
                        cursor=request.query_params.get("cursor"),
                    )
                )
            )
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)

    @extend_schema(request=RuntimePluginInstallSerializer, responses={202: RuntimePluginViewSerializer})
    def post(self, request: Request, organization_uuid: UUID) -> Response:
        body = RuntimePluginInstallSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            row = install_runtime_plugin(
                request.user,
                organization_uuid,
                body.validated_data["manifest"],
                registry_credentials=body.validated_data.get("registry_credentials"),
                audit=preparation_request_audit(request),
            )
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)
        return Response(asdict(row), status=202)


class RuntimePluginActionView(APIView):
    permission_classes = [IsAuthenticatedSession]

    @extend_schema(request=RuntimePluginActionSerializer, responses=RuntimePluginViewSerializer)
    def post(self, request: Request, organization_uuid: UUID, plugin_id: UUID) -> Response:
        body = RuntimePluginActionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            row = change_runtime_plugin(
                request.user,
                organization_uuid,
                plugin_id,
                body.validated_data["action"],
                registry_credentials=body.validated_data.get("registry_credentials"),
                audit=preparation_request_audit(request),
            )
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)
        return Response(asdict(row))
