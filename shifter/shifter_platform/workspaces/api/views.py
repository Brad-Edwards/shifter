"""Named-operation views for workspace membership lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.api.errors import api_error_response
from shared.api.principals import active_actor_user
from shared.api.schema import ApiErrorSerializer
from shared.audit import get_actor_from_request, get_client_ip, get_request_id
from workspaces import services
from workspaces.api.permissions import WORKSPACE_MEMBERSHIP_PERMISSIONS
from workspaces.api.serializers import (
    AddWorkspaceMemberSerializer,
    ChangeWorkspaceMemberRoleSerializer,
    WorkspaceMembershipSerializer,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from rest_framework.request import Request


def _actor(request: Request) -> User:
    actor = active_actor_user(request)
    if actor is None:  # permission_classes reject this before a handler runs
        raise RuntimeError("active workspace actor missing after permission gate")
    return actor


def _audit(request: Request) -> services.MembershipAuditContext:
    actor_type, actor_id = get_actor_from_request(request)
    return services.MembershipAuditContext(
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        request_id=get_request_id(request),
    )


def _raise_as_response(exc: Exception, request: Request) -> NoReturn:
    """Raise a bounded control-flow wrapper consumed by ``handle_exception``."""
    raise _WorkspaceAPIError.from_exception(exc, request) from exc


class _WorkspaceAPIError(Exception):
    """Bounded service-to-HTTP error mapping."""

    _STATUS_BY_CODE = {
        "owner_authority_required": 403,
        "member_add_failed": 404,
        "membership_not_found": 404,
        "membership_exists": 409,
        "last_owner_required": 409,
        "personal_workspace_protected": 409,
        "use_leave_operation": 409,
        "invalid_role": 400,
    }

    def __init__(self, *, code: str, message: str, status_code: int, request: Request) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request = request

    @classmethod
    def from_exception(cls, exc: Exception, request: Request) -> _WorkspaceAPIError:
        if isinstance(exc, services.WorkspaceAuthorizationError):
            return cls(
                code="workspace_access_denied",
                message="Workspace access denied",
                status_code=403,
                request=request,
            )
        if isinstance(exc, services.WorkspaceMembershipError):
            return cls(
                code=exc.code,
                message=exc.message,
                status_code=cls._STATUS_BY_CODE.get(exc.code, 400),
                request=request,
            )
        raise exc

    def to_response(self) -> Response:
        return api_error_response(
            code=self.code,
            message=self.message,
            status_code=self.status_code,
            request=self.request,
        )


class _WorkspaceAPIView(APIView):
    permission_classes = WORKSPACE_MEMBERSHIP_PERMISSIONS

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, _WorkspaceAPIError):
            return exc.to_response()
        return super().handle_exception(exc)


class SelfMembershipView(_WorkspaceAPIView):
    """Read the caller's own effective membership."""

    @extend_schema(
        responses={200: WorkspaceMembershipSerializer, 403: ApiErrorSerializer},
        operation_id="api_v1_workspace_membership_self",
    )
    def get(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            membership = services.get_self_membership(_actor(request), workspace_uuid)
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(membership).data)


class MembershipListAddView(_WorkspaceAPIView):
    """Read the roster or add an existing active account."""

    @extend_schema(
        responses={200: WorkspaceMembershipSerializer(many=True), 403: ApiErrorSerializer},
        operation_id="api_v1_workspace_memberships_list",
    )
    def get(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            memberships = services.list_workspace_memberships(_actor(request), workspace_uuid)
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(memberships, many=True).data)

    @extend_schema(
        request=AddWorkspaceMemberSerializer,
        responses={
            200: WorkspaceMembershipSerializer,
            400: ApiErrorSerializer,
            403: ApiErrorSerializer,
            404: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
        operation_id="api_v1_workspace_memberships_add",
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = AddWorkspaceMemberSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            membership = services.add_workspace_member(
                _actor(request),
                workspace_uuid,
                command.validated_data["email"],
                command.validated_data["role"],
                audit=_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(membership).data)


class MembershipRoleView(_WorkspaceAPIView):
    """Change one membership's role."""

    @extend_schema(
        request=ChangeWorkspaceMemberRoleSerializer,
        responses={
            200: WorkspaceMembershipSerializer,
            400: ApiErrorSerializer,
            403: ApiErrorSerializer,
            404: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
        operation_id="api_v1_workspace_memberships_change_role",
    )
    def post(self, request: Request, workspace_uuid: UUID, user_id: int) -> Response:
        command = ChangeWorkspaceMemberRoleSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            membership = services.change_workspace_member_role(
                _actor(request),
                workspace_uuid,
                user_id,
                command.validated_data["role"],
                audit=_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(membership).data)


class MembershipRemoveView(_WorkspaceAPIView):
    """Remove another workspace member."""

    @extend_schema(
        request=None,
        responses={
            200: WorkspaceMembershipSerializer,
            403: ApiErrorSerializer,
            404: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
        operation_id="api_v1_workspace_memberships_remove",
    )
    def post(self, request: Request, workspace_uuid: UUID, user_id: int) -> Response:
        try:
            membership = services.remove_workspace_member(
                _actor(request),
                workspace_uuid,
                user_id,
                audit=_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(membership).data)


class MembershipLeaveView(_WorkspaceAPIView):
    """Remove the caller's own workspace membership."""

    @extend_schema(
        request=None,
        responses={200: WorkspaceMembershipSerializer, 403: ApiErrorSerializer, 409: ApiErrorSerializer},
        operation_id="api_v1_workspace_memberships_leave",
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            membership = services.leave_workspace(
                _actor(request),
                workspace_uuid,
                audit=_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceMembershipError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceMembershipSerializer(membership).data)
