"""Credential management HTTP edges; services own every authorization decision."""

from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from management import personal_credentials, service_credentials
from management.api.credential_serializers import (
    CreateServiceCredentialSerializer,
    CredentialPageQuerySerializer,
    EmptyCredentialCommandSerializer,
    IssuedPersonalCredentialSerializer,
    IssuePersonalCredentialSerializer,
    PersonalCredentialPageSerializer,
    PersonalCredentialSerializer,
    ServiceCredentialPageSerializer,
    ServiceCredentialSerializer,
    ServicePrincipalUpdateSerializer,
)
from management.services import PrincipalConflictError
from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSession, IsAuthenticatedSessionOrApiToken
from shared.api.principals import authenticated_credential
from shared.api.strict_json import ClosedJSONParser
from shared.authorization import AuthorizationProviderBindingError, TargetRef
from shared.credentials import resolve_credential_scope
from shared.principal_port import PrincipalResolutionError

_SESSION_ONLY_SCHEMA_AUTH: list[dict[str, list[str]]] = [{"cookieAuth": []}]


class CredentialView(APIView):
    permission_classes = [IsAuthenticatedSessionOrApiToken]
    parser_classes = [ClosedJSONParser]

    def handle_exception(self, exc):
        if isinstance(
            exc,
            (
                ValueError,
                PrincipalResolutionError,
                PrincipalConflictError,
                IntegrityError,
                AuthorizationProviderBindingError,
            ),
        ):
            return api_error_response(
                code="credential_denied", message="Credential operation denied", status_code=403, request=self.request
            )
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


def _issue_arguments(request):
    command = IssuePersonalCredentialSerializer(data=request.data)
    command.is_valid(raise_exception=True)
    values = dict(command.validated_data)
    target = TargetRef(values.pop("target_type"), values.pop("target_uuid", None))
    return {
        **values,
        "actor": authenticated_credential(request),
        "user": request.user,
        "target": target,
        "scope": resolve_credential_scope(target),
    }


def _page_arguments(request):
    query = CredentialPageQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query.validated_data


def _issued_response(result):
    token, raw = result
    payload = dict(PersonalCredentialSerializer(token).data)
    payload["token"] = raw
    return Response(payload, status=201)


class PersonalCredentialCollectionView(CredentialView):
    permission_classes = [IsAuthenticatedSession]

    @extend_schema(
        parameters=[CredentialPageQuerySerializer],
        responses=PersonalCredentialPageSerializer,
        auth=_SESSION_ONLY_SCHEMA_AUTH,  # type: ignore[arg-type]
    )
    def get(self, request):
        items = personal_credentials.list_own_tokens(
            authenticated_credential(request), request.user, **_page_arguments(request)
        )
        return Response(PersonalCredentialPageSerializer(items).data)

    @extend_schema(
        request=IssuePersonalCredentialSerializer,
        responses={201: IssuedPersonalCredentialSerializer},
        auth=_SESSION_ONLY_SCHEMA_AUTH,  # type: ignore[arg-type]
    )
    def post(self, request):
        return _issued_response(personal_credentials.issue_personal_token(**_issue_arguments(request)))


class PersonalCredentialRevokeView(CredentialView):
    permission_classes = [IsAuthenticatedSession]

    @extend_schema(
        request=EmptyCredentialCommandSerializer,
        responses={204: None},
        auth=_SESSION_ONLY_SCHEMA_AUTH,  # type: ignore[arg-type]
    )
    def post(self, request, credential_uuid):
        command = EmptyCredentialCommandSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        personal_credentials.revoke_own_token(authenticated_credential(request), request.user, credential_uuid)
        return Response(status=204)


class PersonalCredentialRotateView(CredentialView):
    permission_classes = [IsAuthenticatedSession]

    @extend_schema(
        request=IssuePersonalCredentialSerializer,
        responses={201: IssuedPersonalCredentialSerializer},
        auth=_SESSION_ONLY_SCHEMA_AUTH,  # type: ignore[arg-type]
    )
    def post(self, request, credential_uuid):
        return _issued_response(
            personal_credentials.rotate_personal_token(credential_uuid, **_issue_arguments(request))
        )


class ServiceCredentialCollectionView(CredentialView):
    @extend_schema(parameters=[CredentialPageQuerySerializer], responses=ServiceCredentialPageSerializer)
    def get(self, request):
        items = service_credentials.list_service_credentials(
            authenticated_credential(request), **_page_arguments(request)
        )
        return Response(ServiceCredentialPageSerializer(items).data)

    @extend_schema(request=CreateServiceCredentialSerializer, responses={201: ServiceCredentialSerializer})
    def post(self, request):
        command = CreateServiceCredentialSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        item = service_credentials.create_service_credential(
            authenticated_credential(request), **command.validated_data
        )
        return Response(ServiceCredentialSerializer(item).data, status=201)


class ServiceCredentialDisableView(CredentialView):
    @extend_schema(request=EmptyCredentialCommandSerializer, responses={204: None})
    def post(self, request, credential_uuid):
        command = EmptyCredentialCommandSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        service_credentials.disable_service_credential(authenticated_credential(request), credential_uuid)
        return Response(status=204)


class ServicePrincipalUpdateView(CredentialView):
    @extend_schema(request=ServicePrincipalUpdateSerializer, responses={204: None})
    def post(self, request, principal_uuid):
        command = ServicePrincipalUpdateSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        service_credentials.update_service_principal(
            authenticated_credential(request), principal_uuid, **command.validated_data
        )
        return Response(status=204)
