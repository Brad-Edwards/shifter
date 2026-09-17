"""Tenant-admin pack selection and guest mapping without raw configuration edits."""

from dataclasses import asdict
from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.filters import BaseFilterBackend
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.services._runtime_plugin_packs import (
    list_runtime_plugin_packs,
    runtime_plugin_pack_detail,
    set_runtime_plugin_pack,
)
from shared.api.permissions import IsAuthenticatedSession
from shared.exceptions import ValidationError
from workspaces.services import OrganizationAuthorizationError

from .preparation_adapters import PreparationSerializer, preparation_request_audit
from .runtime_plugins import _error


class RuntimePluginBindingsSerializer(PreparationSerializer):
    """Guest target mappings and bounded adapter parameters."""

    targets = serializers.DictField(child=serializers.CharField(max_length=1024))
    parameters = serializers.DictField(
        child=serializers.CharField(max_length=8192, allow_blank=True, trim_whitespace=False),
        default=dict,
    )


class RuntimePluginPackBindingSerializer(serializers.Serializer):
    """An organization's explicit pack-to-installation selection."""

    id = serializers.UUIDField()
    organization_uuid = serializers.UUIDField()
    pack_id = serializers.CharField()
    pack_digest = serializers.CharField()
    installation_id = serializers.UUIDField()
    bindings = RuntimePluginBindingsSerializer()
    enabled = serializers.BooleanField()
    installation_state = serializers.CharField()


class RuntimePluginPackSerializer(serializers.Serializer):
    """Catalog pack identity with its current administrator binding."""

    id = serializers.CharField()
    name = serializers.CharField()
    pack_digest = serializers.CharField()
    binding = RuntimePluginPackBindingSerializer(allow_null=True)
    can_update = serializers.BooleanField(default=False)


class RuntimePluginTargetSerializer(serializers.Serializer):
    """A selectable compiled guest address and operating system."""

    address = serializers.CharField()
    os_family = serializers.CharField()


class RuntimePluginPackDetailSerializer(RuntimePluginPackSerializer):
    """Verified pack revision and the guests available for binding."""

    targets = RuntimePluginTargetSerializer(many=True)


class RuntimePluginPackUpdateSerializer(PreparationSerializer):
    """An optimistic update pinned to the verified pack digest."""

    installation_id = serializers.UUIDField()
    pack_digest = serializers.RegexField(r"^sha256:[a-f0-9]{64}$")
    bindings = RuntimePluginBindingsSerializer()
    enabled = serializers.BooleanField(default=True)


class RuntimePluginPackListView(ListAPIView):
    """List packs that the organization administrator can bind."""

    permission_classes = [IsAuthenticatedSession]
    serializer_class = RuntimePluginPackSerializer
    filter_backends: list[type[BaseFilterBackend]] = []

    def get_queryset(self) -> list[dict[str, Any]]:
        return list_runtime_plugin_packs(self.request.user, self.kwargs["organization_uuid"])

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        try:
            return super().get(request, *args, **kwargs)
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)


class RuntimePluginPackDetailView(APIView):
    """Inspect verified guests and update the organization's binding."""

    permission_classes = [IsAuthenticatedSession]

    @extend_schema(responses=RuntimePluginPackDetailSerializer)
    def get(self, request: Request, organization_uuid: UUID, pack_id: str) -> Response:
        try:
            return Response(runtime_plugin_pack_detail(request.user, organization_uuid, pack_id))
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)

    @extend_schema(request=RuntimePluginPackUpdateSerializer, responses=RuntimePluginPackBindingSerializer)
    def post(self, request: Request, organization_uuid: UUID, pack_id: str) -> Response:
        body = RuntimePluginPackUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            row = set_runtime_plugin_pack(
                request.user, organization_uuid, pack_id, body.validated_data, audit=preparation_request_audit(request)
            )
            return Response(asdict(row))
        except (OrganizationAuthorizationError, ValidationError) as exc:
            return _error(request, exc)
