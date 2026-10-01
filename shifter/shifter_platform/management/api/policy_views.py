"""S8 policy-aware identity API, prepared without replacing legacy routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from management import admin_services, lifecycle, password_reset, services
from management.api.serializers import (
    AdminUserDetailSerializer,
    AdminUserListItemSerializer,
    AdminUserListQuerySerializer,
    LifecycleTransitionRequestSerializer,
    SetActiveRequestSerializer,
)
from management.policy import require_principal_administration
from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSessionOrApiToken
from shared.api.principals import authenticated_credential
from shared.api.strict_json import ClosedJSONParser
from shared.api_tokens.authentication import ApiTokenAuthentication
from shared.audit import request_audit
from shared.authorization import AuthorizationProviderBindingError, configured_authorization_provider

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class _PolicyAdminView(APIView):
    """Shared admission and bounded policy errors for the prepared S8 views."""

    authentication_classes = [ApiTokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticatedSessionOrApiToken]
    parser_classes = [ClosedJSONParser]

    def handle_exception(self, exc: Exception) -> Response:
        if isinstance(exc, PermissionError):
            code, message, status_code = "administration_denied", "Administration denied", 403
        elif isinstance(exc, AuthorizationProviderBindingError):
            code, message, status_code = "authorization_unavailable", "Authorization unavailable", 503
        elif isinstance(exc, lifecycle.AccountLifecycleError):
            code, message = exc.code, exc.message
            status_code = 403 if exc.code == "authority_denied" else 409
        elif isinstance(exc, password_reset.PasswordResetError):
            code, message = exc.code, exc.message
            status_code = 409 if exc.code == "reset_throttled" else 400
        else:
            return super().handle_exception(exc)
        return api_error_response(code=code, message=message, status_code=status_code, request=self.request)


class PolicyAdminUserListView(_PolicyAdminView, ListAPIView):
    """Global identity list; policy is checked before filtering and pagination."""

    serializer_class = AdminUserListItemSerializer

    @extend_schema(parameters=[AdminUserListQuerySerializer], responses=AdminUserListItemSerializer(many=True))
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[User]:
        query = AdminUserListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return admin_services.list_policy_admin_users(
            authenticated_credential(self.request),
            configured_authorization_provider(),
            **query.validated_data,
        )


class PolicyAdminUserDetailView(_PolicyAdminView, RetrieveAPIView):
    """Read one user only after the installation policy check."""

    serializer_class = AdminUserDetailSerializer

    @extend_schema(responses=AdminUserDetailSerializer)
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[User]:
        return admin_services.list_policy_admin_users(
            authenticated_credential(self.request),
            configured_authorization_provider(),
            include_deleted=True,
        )

    def get_serializer_context(self) -> dict[str, Any]:
        return {**super().get_serializer_context(), "policy_actor": authenticated_credential(self.request)}


class PolicyAdminUserLifecycleView(_PolicyAdminView, APIView):
    """Run the one locked lifecycle service for human or service administrators."""

    @extend_schema(request=LifecycleTransitionRequestSerializer, responses=AdminUserDetailSerializer)
    def post(self, request: Request, pk: int) -> Response:
        command = LifecycleTransitionRequestSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        return _policy_transition(request, pk, lifecycle.AccountLifecycleAction(command.validated_data["action"]))


def _policy_transition(request: Request, pk: int, action: lifecycle.AccountLifecycleAction) -> Response:
    """Use the one locked lifecycle command for every S8 compatibility route."""
    actor = authenticated_credential(request)
    provider = configured_authorization_provider()
    require_principal_administration(actor, provider)
    user = services.get_admin_user(pk)
    if user is None:
        raise NotFound("User not found")
    attribution = request_audit(request, credential=actor)
    lifecycle.transition_account(
        user,
        action=action,
        actor=actor,
        provider=provider,
        audit=services.AuditContext(
            actor_type=attribution.actor_type,
            actor_id=attribution.actor_id,
            request_id=attribution.request_id,
            source_ip=attribution.source_ip,
            user_agent=attribution.user_agent,
        ),
    )
    user.refresh_from_db()
    return Response(AdminUserDetailSerializer(user, context={"request": request, "policy_actor": actor}).data)


class PolicyAdminUserSetActiveView(_PolicyAdminView, APIView):
    """Preserve the v1 set-active command through the policy lifecycle service."""

    @extend_schema(request=SetActiveRequestSerializer, responses=AdminUserDetailSerializer)
    def post(self, request: Request, pk: int) -> Response:
        command = SetActiveRequestSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        action = (
            lifecycle.AccountLifecycleAction.ACTIVATE
            if command.validated_data["is_active"]
            else lifecycle.AccountLifecycleAction.DEACTIVATE
        )
        return _policy_transition(request, pk, action)


class PolicyAdminUserDeleteView(_PolicyAdminView, APIView):
    """Preserve the v1 soft-delete command through the policy lifecycle service."""

    @extend_schema(request=None, responses=AdminUserDetailSerializer)
    def post(self, request: Request, pk: int) -> Response:
        return _policy_transition(request, pk, lifecycle.AccountLifecycleAction.DELETE)


class PolicyAdminUserResetPasswordView(_PolicyAdminView, APIView):
    """Request a local reset through the installation policy and proven delivery service."""

    @extend_schema(request=None, responses=AdminUserDetailSerializer)
    def post(self, request: Request, pk: int) -> Response:
        actor = authenticated_credential(request)
        provider = configured_authorization_provider()
        require_principal_administration(actor, provider)
        user = services.get_admin_user(pk)
        if user is None:
            raise NotFound("User not found")
        attribution = request_audit(request, credential=actor)
        password_reset.request_password_reset(
            user,
            audit=services.AuditContext(
                actor_type=attribution.actor_type,
                actor_id=attribution.actor_id,
                request_id=attribution.request_id,
                source_ip=attribution.source_ip,
                user_agent=attribution.user_agent,
            ),
            request=request._request,
            actor=actor,
            provider=provider,
        )
        user.refresh_from_db()
        return Response(AdminUserDetailSerializer(user, context={"request": request, "policy_actor": actor}).data)
