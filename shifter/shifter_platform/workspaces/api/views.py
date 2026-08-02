"""Named-operation views for workspace membership lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSession, IsStaffSession
from shared.api.principals import active_actor_user
from shared.api.schema import ApiErrorSerializer
from shared.api_tokens.authentication import ApiTokenAuthentication
from shared.audit import get_actor_from_request, get_client_ip, get_request_id
from workspaces import services
from workspaces.api.permissions import WORKSPACE_MEMBERSHIP_PERMISSIONS
from workspaces.api.serializers import (
    AddWorkspaceMemberSerializer,
    ChangeWorkspaceMemberRoleSerializer,
    CreateWorkspaceSerializer,
    OrganizationProfileSerializer,
    OrganizationProfileUpdateSerializer,
    PrincipalWorkspaceContextSerializer,
    RenameWorkspaceSerializer,
    TransferWorkspaceOwnershipSerializer,
    WorkspaceMembershipSerializer,
    WorkspaceSerializer,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from rest_framework.request import Request


def _actor(request: Request) -> User:
    """Return the authenticated active user established by the permission gate."""
    actor = active_actor_user(request)
    # Permission classes reject this before a handler runs.
    if actor is None:
        raise RuntimeError("active workspace actor missing after permission gate")
    return actor


def _audit(request: Request) -> services.MembershipAuditContext:
    """Build trusted membership audit attribution from the current request."""
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
        # Workspace lifecycle (#1940)
        "name_invalid": 400,
        "name_blank": 400,
        "name_too_long": 400,
        "name_taken": 409,
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
        if isinstance(exc, services.OrganizationAuthorizationError):
            return cls(
                code="organization_access_denied",
                message="Organization access denied",
                status_code=403,
                request=request,
            )
        if isinstance(exc, (services.WorkspaceMembershipError, services.WorkspaceLifecycleError)):
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
    """Base view that maps bounded workspace service failures to API errors."""

    permission_classes = WORKSPACE_MEMBERSHIP_PERMISSIONS

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, _WorkspaceAPIError):
            return exc.to_response()
        return super().handle_exception(exc)


class PrincipalWorkspaceContextView(ListAPIView):
    """Read the caller's own organization/workspace context for the admin console.

    A side-effect-free projection of the caller's existing workspace memberships
    (organization, workspace, role, and role-permitted operations), used by the
    ``/administer`` organization console shell and switcher (ADR-046-R11, #1938).

    Staff-session only and deliberately **not** token-capable: the bearer-first,
    fail-closed authentication ordering parses an invalid token before session
    fallback, and ``IsStaffSession`` rejects any valid platform token (including
    one owned by a staff user). Staff admission and workspace authority stay
    additive -- staff admits the console, but each child resource endpoint still
    reauthorizes its own workspace operation. The read never creates or repairs
    tenancy state, so a staff caller with no membership receives an empty page.
    """

    authentication_classes = [ApiTokenAuthentication, SessionAuthentication]
    permission_classes = [IsStaffSession]
    serializer_class = PrincipalWorkspaceContextSerializer
    # The service returns a materialized, already-ordered list, not a queryset, so
    # the globally configured OrderingFilter/SearchFilter backends (which call
    # queryset.order_by / .filter) cannot apply. Opt out so the generated contract
    # does not advertise ?ordering=/?search= parameters this view cannot honor.
    filter_backends: list[type] = []

    @extend_schema(
        responses={200: PrincipalWorkspaceContextSerializer(many=True), 403: ApiErrorSerializer},
        operation_id="api_v1_workspaces_principal_context",
    )
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> list[services.ActorWorkspaceContext]:
        actor = active_actor_user(self.request)
        if actor is None:
            # Defensive: IsStaffSession admits only an authenticated staff session.
            return []
        return services.list_actor_workspace_contexts(actor)


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


def _organization_audit(request: Request) -> services.OrganizationAuditContext:
    """Build trusted organization-update audit attribution from the request."""
    actor_type, actor_id = get_actor_from_request(request)
    return services.OrganizationAuditContext(
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        request_id=get_request_id(request),
    )


class OrganizationListView(ListAPIView):
    """List the organizations the caller may administer (ADR-048 authority).

    Session-only, like the profile detail view. The list is the authority-owned
    discovery source for the settings surface: a superuser sees every
    organization, every other actor sees only the organizations it holds an
    ``admin`` membership in, and workspace reachability is never used. A caller
    who administers none receives an empty page.
    """

    authentication_classes = [ApiTokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticatedSession]
    serializer_class = OrganizationProfileSerializer
    # The service returns a materialized, already-ordered projection list, not a
    # queryset, so the global ordering/search filter backends cannot apply.
    filter_backends: list[type] = []

    @extend_schema(
        responses={200: OrganizationProfileSerializer(many=True), 403: ApiErrorSerializer},
        operation_id="api_v1_organizations_administrable_list",
    )
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> list[services.OrganizationProfile]:
        return services.list_administrable_organizations(self.request.user)


class OrganizationProfileView(APIView):
    """Read or partially update an organization profile, keyed by public UUID.

    Session-only and authorized by the ADR-048 organization-admin seam (or a
    Django superuser override) inside ``workspaces.services``; a platform token
    principal is refused by ``IsAuthenticatedSession``. A missing organization,
    an organization outside the actor's authority, and insufficient authority
    all return the same opaque 403 so the endpoint is not a tenant-enumeration
    oracle.
    """

    # Bearer-first, fail-closed chain: an invalid ``shf_`` bearer is rejected
    # outright; a valid token authenticates as an ApiToken principal, which
    # IsAuthenticatedSession then refuses (this endpoint is session-only).
    authentication_classes = [ApiTokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticatedSession]

    def _denied(self, request: Request) -> Response:
        return api_error_response(
            code="organization_access_denied",
            message="Organization access denied",
            status_code=403,
            request=request,
        )

    @extend_schema(
        responses={200: OrganizationProfileSerializer, 403: ApiErrorSerializer},
        operation_id="api_v1_organization_retrieve",
    )
    def get(self, request: Request, organization_uuid: UUID) -> Response:
        try:
            profile = services.get_organization_profile(request.user, organization_uuid)
        except services.OrganizationAuthorizationError:
            return self._denied(request)
        return Response(OrganizationProfileSerializer(profile).data)

    @extend_schema(
        request=OrganizationProfileUpdateSerializer,
        responses={200: OrganizationProfileSerializer, 400: ApiErrorSerializer, 403: ApiErrorSerializer},
        operation_id="api_v1_organization_update",
    )
    def patch(self, request: Request, organization_uuid: UUID) -> Response:
        command = OrganizationProfileUpdateSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            profile = services.update_organization_profile(
                request.user,
                organization_uuid,
                command.validated_data,
                audit=_organization_audit(request),
            )
        except services.OrganizationAuthorizationError:
            return self._denied(request)
        return Response(OrganizationProfileSerializer(profile).data)


def _workspace_lifecycle_audit(request: Request) -> services.WorkspaceAuditContext:
    """Build trusted workspace-lifecycle audit attribution from the request."""
    actor_type, actor_id = get_actor_from_request(request)
    return services.WorkspaceAuditContext(
        actor_type=actor_type,
        actor_id=actor_id,
        source_ip=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        request_id=get_request_id(request),
    )


def _query_flag(request: Request, name: str) -> bool:
    """Interpret a boolean-ish query parameter (absent or falsey -> False)."""
    value = request.query_params.get(name)
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


class _WorkspaceLifecycleAPIView(APIView):
    """Base view for the session-authorized workspace lifecycle surface (#1940).

    Session-only, like the organization profile endpoints (ADR-048, PLAT-232):
    the bearer-first, fail-closed chain authenticates an ``shf_`` token as an
    ApiToken principal, which ``IsAuthenticatedSession`` then refuses. Domain
    authority is enforced inside ``workspaces.services`` -- organization-admin
    membership for create/list, and the workspace role seam for read/mutate --
    so a valid session alone never grants workspace authority. The SPA
    ``/administer`` console remains staff-gated at the route level for
    defense-in-depth.
    """

    authentication_classes = [ApiTokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticatedSession]

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, _WorkspaceAPIError):
            return exc.to_response()
        return super().handle_exception(exc)


class WorkspaceCollectionView(_WorkspaceLifecycleAPIView):
    """List an organization's workspaces or create a new one (org-admin authority)."""

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "organization",
                OpenApiTypes.UUID,
                OpenApiParameter.QUERY,
                required=True,
                description="Public UUID of the organization to scope the list to.",
            ),
            OpenApiParameter(
                "include_archived",
                OpenApiTypes.BOOL,
                OpenApiParameter.QUERY,
                required=False,
                description="Include archived workspaces (default false: active only).",
            ),
            OpenApiParameter(
                "search",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                required=False,
                description="Case-insensitive workspace-name substring filter.",
            ),
        ],
        responses={200: WorkspaceSerializer(many=True), 400: ApiErrorSerializer, 403: ApiErrorSerializer},
        operation_id="api_v1_workspaces_list",
    )
    def get(self, request: Request) -> Response:
        organization_uuid = request.query_params.get("organization")
        if not organization_uuid:
            return api_error_response(
                code="organization_required",
                message="An 'organization' UUID query parameter is required",
                status_code=400,
                request=request,
            )
        try:
            workspaces = services.list_workspaces(
                request.user,
                organization_uuid,
                include_archived=_query_flag(request, "include_archived"),
                search=request.query_params.get("search"),
            )
        except services.OrganizationAuthorizationError as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspaces, many=True).data)

    @extend_schema(
        request=CreateWorkspaceSerializer,
        responses={
            201: WorkspaceSerializer,
            400: ApiErrorSerializer,
            403: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
        operation_id="api_v1_workspaces_create",
    )
    def post(self, request: Request) -> Response:
        command = CreateWorkspaceSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            workspace = services.create_workspace(
                request.user,
                command.validated_data["organization_uuid"],
                command.validated_data["name"],
                audit=_workspace_lifecycle_audit(request),
            )
        except (services.OrganizationAuthorizationError, services.WorkspaceLifecycleError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data, status=status.HTTP_201_CREATED)


class WorkspaceDetailView(_WorkspaceLifecycleAPIView):
    """Read a workspace's administrative detail or rename it (workspace role seam)."""

    @extend_schema(
        responses={200: WorkspaceSerializer, 403: ApiErrorSerializer},
        operation_id="api_v1_workspace_detail",
    )
    def get(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            workspace = services.get_workspace(request.user, workspace_uuid)
        except services.WorkspaceAuthorizationError as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data)

    @extend_schema(
        request=RenameWorkspaceSerializer,
        responses={
            200: WorkspaceSerializer,
            400: ApiErrorSerializer,
            403: ApiErrorSerializer,
            409: ApiErrorSerializer,
        },
        operation_id="api_v1_workspace_rename",
    )
    def patch(self, request: Request, workspace_uuid: UUID) -> Response:
        command = RenameWorkspaceSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            workspace = services.rename_workspace(
                request.user,
                workspace_uuid,
                command.validated_data["name"],
                audit=_workspace_lifecycle_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceLifecycleError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data)


class WorkspaceArchiveView(_WorkspaceLifecycleAPIView):
    """Archive a workspace (reversible soft-state marker)."""

    @extend_schema(
        request=None,
        responses={200: WorkspaceSerializer, 403: ApiErrorSerializer, 409: ApiErrorSerializer},
        operation_id="api_v1_workspace_archive",
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            workspace = services.archive_workspace(
                request.user,
                workspace_uuid,
                audit=_workspace_lifecycle_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceLifecycleError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data)


class WorkspaceRestoreView(_WorkspaceLifecycleAPIView):
    """Restore an archived workspace."""

    @extend_schema(
        request=None,
        responses={200: WorkspaceSerializer, 403: ApiErrorSerializer, 409: ApiErrorSerializer},
        operation_id="api_v1_workspace_restore",
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        try:
            workspace = services.restore_workspace(
                request.user,
                workspace_uuid,
                audit=_workspace_lifecycle_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceLifecycleError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data)


class WorkspaceTransferOwnershipView(_WorkspaceLifecycleAPIView):
    """Transfer workspace ownership to an existing member (owner-only)."""

    @extend_schema(
        request=TransferWorkspaceOwnershipSerializer,
        responses={
            200: WorkspaceSerializer,
            400: ApiErrorSerializer,
            403: ApiErrorSerializer,
            404: ApiErrorSerializer,
        },
        operation_id="api_v1_workspace_transfer_ownership",
    )
    def post(self, request: Request, workspace_uuid: UUID) -> Response:
        command = TransferWorkspaceOwnershipSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        try:
            workspace = services.transfer_workspace_ownership(
                request.user,
                workspace_uuid,
                command.validated_data["user_id"],
                audit=_workspace_lifecycle_audit(request),
            )
        except (services.WorkspaceAuthorizationError, services.WorkspaceLifecycleError) as exc:
            _raise_as_response(exc, request)
        return Response(WorkspaceSerializer(workspace).data)
