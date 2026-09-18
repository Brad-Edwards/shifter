"""Tenant model-policy administration of existing ranges, with explicit CAS."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.api.preparation_adapters import PreparationSerializer
from cms.services import change_range_model_sources, get_range_model_sources
from shared.api.errors import api_error_response
from shared.api.model_sources import ModelSourceRevisionField, ModelSourceSelectionField, ModelSourceSelectionSerializer
from shared.api.permissions import IsAuthenticatedSession
from shared.api.strict_json import ClosedJSONParser
from shared.model_access import ContractError
from workspaces.services import OrganizationAuthorizationError, WorkspaceAuthorizationError


class RangeModelSourcesWriteSerializer(PreparationSerializer):
    expected_revision = ModelSourceRevisionField(min_value=0)
    selection = ModelSourceSelectionField()


class ModelAssignmentSerializer(serializers.Serializer):
    workload = serializers.CharField()
    logical_alias = serializers.CharField()
    provider = serializers.CharField()
    model = serializers.CharField()
    region = serializers.CharField()


class ModelPolicyRuntimeSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["active", "refresh_pending", "unavailable"])
    assignments = ModelAssignmentSerializer(many=True)


class RangeModelSourcesSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    workspace = serializers.UUIDField()
    scenario = serializers.CharField()
    revision = serializers.IntegerField()
    selection = ModelSourceSelectionSerializer()
    error = serializers.CharField()
    runtime = ModelPolicyRuntimeSerializer()


def source_policy_error(request, exc):
    conflict = isinstance(exc, ContractError) and exc.code == "source.revision_conflict"
    return api_error_response(
        code="source_revision_conflict" if conflict else "range_model_sources_unavailable",
        message="Model sources changed. Reload this range before saving."
        if conflict
        else "Range model sources are unavailable.",
        status_code=409 if conflict else 403,
        request=request,
    )


class RangeModelSourcesView(APIView):
    permission_classes = [IsAuthenticatedSession]
    parser_classes = [ClosedJSONParser]

    @extend_schema(responses=RangeModelSourcesSerializer)
    def get(self, request, request_id):
        try:
            return Response(get_range_model_sources(request.user, request_id=request_id))
        except (WorkspaceAuthorizationError, OrganizationAuthorizationError, ContractError) as exc:
            return source_policy_error(request, exc)

    @extend_schema(
        request=RangeModelSourcesWriteSerializer,
        responses={200: RangeModelSourcesSerializer, 202: RangeModelSourcesSerializer},
    )
    def put(self, request, request_id):
        serializer = RangeModelSourcesWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = change_range_model_sources(request.user, request_id=request_id, **serializer.validated_data)
            return Response(result, status=202 if result["error"] else 200)
        except (WorkspaceAuthorizationError, OrganizationAuthorizationError, ContractError) as exc:
            return source_policy_error(request, exc)


class ModelRangeSummarySerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    scenario = serializers.CharField()
    status = serializers.CharField()
    revision = serializers.IntegerField()
    error = serializers.CharField()


class ModelRangePageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    has_next = serializers.BooleanField()
    results = ModelRangeSummarySerializer(many=True)


class ModelRangePageQuerySerializer(PreparationSerializer):
    page = serializers.IntegerField(min_value=1, max_value=1000000, default=1)


class OrganizationModelRangesView(APIView):
    permission_classes = [IsAuthenticatedSession]

    @extend_schema(parameters=[ModelRangePageQuerySerializer], responses=ModelRangePageSerializer)
    def get(self, request, organization_uuid):
        from cms.services import list_organization_model_ranges

        query = ModelRangePageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            return Response(
                list_organization_model_ranges(
                    request.user, organization_uuid=organization_uuid, **query.validated_data
                )
            )
        except (OrganizationAuthorizationError, WorkspaceAuthorizationError) as exc:
            return source_policy_error(request, exc)
