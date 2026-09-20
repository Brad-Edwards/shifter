"""Composition-root API for scoped OpenFGA authorization administration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.api.errors import api_error_response
from shared.api.principals import active_actor_user
from shared.api.strict_json import ClosedJSONParser
from shared.api_tokens.models import ApiToken
from shared.api_tokens.scopes import AUTHORIZATION_ACTION_SCOPES
from shared.audit import RequestAudit, get_actor_from_request, get_client_ip, get_request_id
from shared.authorization import (
    ACTION_CATALOG,
    PREDEFINED_POLICIES,
    AdministrativeRoleChange,
    AuthorizationContractError,
    AuthorizationProviderBindingError,
    CredentialCeiling,
    GroupMembershipChange,
    PolicyEffect,
    RelationshipSubject,
    RoleAssignmentChange,
    TargetRef,
    configured_authorization_provider,
)
from shared.principal_port import PrincipalResolutionError, principal_for_user
from workspaces import services
from workspaces.api.permissions import AUTHORIZATION_PERMISSIONS
from workspaces.api.serializers import (
    AuthorizationActionSerializer,
    AuthorizationMetadataSerializer,
    AuthorizationMutationSerializer,
    AuthorizationOperationSerializer,
    ClosedCommandSerializer,
    CreateAuthorizationMetadataSerializer,
    DirectActionAssignmentMutationSerializer,
    NativeMembershipMutationSerializer,
    PolicyActionMutationSerializer,
    PredefinedAuthorizationPolicySerializer,
    PredefinedRoleMutationSerializer,
    RoleAssignmentMutationSerializer,
)

if TYPE_CHECKING:
    from rest_framework.request import Request


class AuthorizationAPIError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _credential_ceiling(request: Request) -> CredentialCeiling:
    auth = getattr(request, "auth", None)
    if isinstance(auth, ApiToken):
        by_scope = {value: action for action, value in AUTHORIZATION_ACTION_SCOPES.items()}
        return CredentialCeiling(frozenset(by_scope[item] for item in auth.scopes if item in by_scope))
    return CredentialCeiling(frozenset(item.code for item in ACTION_CATALOG))


def _context(request: Request, workspace_uuid: UUID):
    actor_user = active_actor_user(request)
    if actor_user is None:
        raise AuthorizationAPIError("authorization_denied", 403)
    try:
        actor = principal_for_user(actor_user)
        scope = services.workspace_authorization_scope(workspace_uuid)
        provider = configured_authorization_provider()
    except (PrincipalResolutionError, services.AuthorizationAdminError):
        raise AuthorizationAPIError("authorization_denied", 403) from None
    except AuthorizationProviderBindingError:
        raise AuthorizationAPIError("authorization_unavailable", 503) from None
    return actor, _credential_ceiling(request), scope, provider


def _request_audit(request: Request) -> RequestAudit:
    actor_type, actor_id = get_actor_from_request(request)
    return RequestAudit(
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        request_id=get_request_id(request)[:128],
    )


class _AuthorizationAPIView(APIView):
    permission_classes = AUTHORIZATION_PERMISSIONS
    parser_classes = [ClosedJSONParser]

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, AuthorizationAPIError):
            return api_error_response(
                code=exc.code,
                message="Authorization request could not be completed",
                status_code=exc.status_code,
                request=self.request,
            )
        if isinstance(exc, services.AuthorizationMutationConflict):
            return api_error_response(
                code="authorization_conflict",
                message="Authorization request conflicts with current state",
                status_code=409,
                request=self.request,
            )
        if isinstance(exc, services.AuthorizationAdminValidationError):
            return api_error_response(
                code="invalid_request",
                message="Authorization request is invalid",
                status_code=400,
                request=self.request,
            )
        if isinstance(exc, services.AuthorizationAdminConflict):
            return api_error_response(
                code="authorization_conflict",
                message="Authorization metadata already exists",
                status_code=409,
                request=self.request,
            )
        if isinstance(exc, services.AuthorizationAdminError):
            return api_error_response(
                code="authorization_denied",
                message="Authorization request denied",
                status_code=403,
                request=self.request,
            )
        if isinstance(exc, AuthorizationContractError):
            return api_error_response(
                code="invalid_request",
                message="Authorization request is invalid",
                status_code=400,
                request=self.request,
            )
        return super().handle_exception(exc)


class AuthorizationCatalogView(_AuthorizationAPIView):
    @extend_schema(responses={200: AuthorizationActionSerializer(many=True)})
    def get(self, request: Request) -> Response:
        payload = [
            {
                "code": item.code,
                "target_type": item.target_type,
                "administrative": item.administrative,
                "delegable": item.delegable,
                "principal_kinds": sorted(item.principal_kinds),
            }
            for item in ACTION_CATALOG
        ]
        return Response(AuthorizationActionSerializer(payload, many=True).data)


class PredefinedAuthorizationCatalogView(_AuthorizationAPIView):
    @extend_schema(responses={200: PredefinedAuthorizationPolicySerializer(many=True)})
    def get(self, request: Request) -> Response:
        payload = [
            {
                "code": item.code,
                "name": item.name,
                "assignment_group_name": item.assignment_group_name,
                "target_type": item.target_type,
                "actions": sorted(item.actions),
            }
            for item in PREDEFINED_POLICIES
        ]
        return Response(PredefinedAuthorizationPolicySerializer(payload, many=True).data)


class AuthorizationGroupCollectionView(_AuthorizationAPIView):
    @extend_schema(responses={200: AuthorizationMetadataSerializer(many=True)})
    def get(self, request: Request, workspace_uuid: UUID) -> Response:
        actor, credential, scope, provider = _context(request, workspace_uuid)
        items = services.list_authorization_groups(actor, credential, scope, provider)
        return Response(AuthorizationMetadataSerializer(items, many=True).data)

    @extend_schema(
        request=CreateAuthorizationMetadataSerializer,
        responses={201: AuthorizationMetadataSerializer},
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = CreateAuthorizationMetadataSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        actor, credential, scope, provider = _context(request, workspace_uuid)
        item = services.create_authorization_group(
            actor,
            credential,
            scope,
            provider,
            audit=_request_audit(request),
            **command.validated_data,
        )
        return Response(AuthorizationMetadataSerializer(item).data, status=201)


class AuthorizationPolicyCollectionView(_AuthorizationAPIView):
    @extend_schema(responses={200: AuthorizationMetadataSerializer(many=True)})
    def get(self, request: Request, workspace_uuid: UUID) -> Response:
        actor, credential, scope, provider = _context(request, workspace_uuid)
        items = services.list_authorization_policies(actor, credential, scope, provider)
        return Response(AuthorizationMetadataSerializer(items, many=True).data)

    @extend_schema(
        request=CreateAuthorizationMetadataSerializer,
        responses={201: AuthorizationMetadataSerializer},
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = CreateAuthorizationMetadataSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        actor, credential, scope, provider = _context(request, workspace_uuid)
        item = services.create_authorization_policy(
            actor,
            credential,
            scope,
            provider,
            audit=_request_audit(request),
            **command.validated_data,
        )
        return Response(AuthorizationMetadataSerializer(item).data, status=201)


def _native_result(request: Request, workspace_uuid: UUID, change, idempotency_key: str) -> Response:
    actor, credential, scope, provider = _context(request, workspace_uuid)
    mutation = services.NativeRelationshipMutationRequest(
        actor=actor,
        credential=credential,
        change=change,
        scope=scope,
        idempotency_key=idempotency_key,
        model_id=settings.OPENFGA_MODEL_ID,
        audit=_request_audit(request),
    )
    result = services.apply_native_relationship_mutation(mutation, provider)
    return Response(AuthorizationMutationSerializer(result).data, status=202)


class AuthorizationGroupMembershipView(_AuthorizationAPIView):
    @extend_schema(request=NativeMembershipMutationSerializer, responses={202: AuthorizationMutationSerializer})
    def post(self, request: Request, workspace_uuid: UUID, group_uuid: UUID) -> Response:
        command = NativeMembershipMutationSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        data = command.validated_data
        change = GroupMembershipChange(
            group_uuid,
            data["principal_uuid"],
            PolicyEffect(data["effect"]),
        )
        return _native_result(request, workspace_uuid, change, data["idempotency_key"])


class AuthorizationPolicyAssignmentView(_AuthorizationAPIView):
    @extend_schema(request=RoleAssignmentMutationSerializer, responses={202: AuthorizationMutationSerializer})
    def post(self, request: Request, workspace_uuid: UUID, policy_uuid: UUID) -> Response:
        command = RoleAssignmentMutationSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        data = command.validated_data
        change = RoleAssignmentChange(
            policy_uuid,
            RelationshipSubject(data["subject_kind"], data["subject_uuid"]),
            PolicyEffect(data["effect"]),
        )
        return _native_result(request, workspace_uuid, change, data["idempotency_key"])


class AuthorizationPredefinedAssignmentView(_AuthorizationAPIView):
    @extend_schema(request=PredefinedRoleMutationSerializer, responses={202: AuthorizationMutationSerializer})
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = PredefinedRoleMutationSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        data = command.validated_data
        change = AdministrativeRoleChange(
            RelationshipSubject(data["subject_kind"], data["subject_uuid"]),
            data["policy_code"],
            TargetRef("workspace", workspace_uuid),
            PolicyEffect(data["effect"]),
        )
        return _native_result(request, workspace_uuid, change, data["idempotency_key"])


def _action_result(
    request: Request,
    workspace_uuid: UUID,
    subject: RelationshipSubject,
    data: dict[str, object],
) -> Response:
    actor, credential, scope, provider = _context(request, workspace_uuid)
    mutation = services.PolicyMutationRequest(
        actor=actor,
        credential=credential,
        subject=subject,
        action=str(data["action"]),
        target=TargetRef("workspace", workspace_uuid),
        scope=scope,
        effect=PolicyEffect(data["effect"]),
        idempotency_key=str(data["idempotency_key"]),
        model_id=settings.OPENFGA_MODEL_ID,
        audit=_request_audit(request),
    )
    result = services.apply_policy_mutation(mutation, provider)
    return Response(AuthorizationMutationSerializer(result).data, status=202)


class AuthorizationPolicyActionView(_AuthorizationAPIView):
    @extend_schema(request=PolicyActionMutationSerializer, responses={202: AuthorizationMutationSerializer})
    def post(self, request: Request, workspace_uuid: UUID, policy_uuid: UUID) -> Response:
        command = PolicyActionMutationSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        data = command.validated_data
        return _action_result(request, workspace_uuid, RelationshipSubject("role", policy_uuid), data)


class AuthorizationDirectAssignmentView(_AuthorizationAPIView):
    @extend_schema(request=DirectActionAssignmentMutationSerializer, responses={202: AuthorizationMutationSerializer})
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = DirectActionAssignmentMutationSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        data = command.validated_data
        subject = RelationshipSubject(data["subject_kind"], data["subject_uuid"])
        return _action_result(request, workspace_uuid, subject, data)


class AuthorizationOperationView(_AuthorizationAPIView):
    @extend_schema(responses={200: AuthorizationOperationSerializer})
    def get(self, request: Request, workspace_uuid: UUID, operation_uuid: UUID) -> Response:
        actor, credential, scope, provider = _context(request, workspace_uuid)
        operation = services.get_authorization_operation(
            actor,
            credential,
            scope,
            provider,
            operation_uuid,
        )
        return Response(AuthorizationOperationSerializer(operation).data)

    @extend_schema(request=None, responses={200: AuthorizationOperationSerializer})
    def post(self, request: Request, workspace_uuid: UUID, operation_uuid: UUID) -> Response:
        command = ClosedCommandSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        actor, credential, scope, provider = _context(request, workspace_uuid)
        operation = services.reconcile_authorization_operation(
            actor,
            credential,
            scope,
            provider,
            operation_uuid,
        )
        return Response(AuthorizationOperationSerializer(operation).data)
