"""Explicit event-scoped participant admission and reads for neutral credentials."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf import services
from ctf.api import projections
from ctf.api.serializers import ParticipantCurrentEventSerializer
from shared.api.closed_serializer import ClosedSerializer
from shared.api.errors import api_error_response
from shared.api.permissions import IsAuthenticatedSessionOrApiToken
from shared.api.principals import authenticated_credential
from shared.api.strict_json import ClosedJSONParser
from shared.authorization import AuthorizationProviderBindingError
from shared.identity_scope import PrincipalRef
from shared.principal_port import PrincipalResolutionError


class AdmitPrincipalParticipantSerializer(ClosedSerializer):
    principal_uuid = serializers.UUIDField()
    name = serializers.CharField(max_length=100)


class PrincipalParticipantSerializer(serializers.Serializer):
    participant_uuid = serializers.UUIDField()
    principal_uuid = serializers.UUIDField()
    event_uuid = serializers.UUIDField()


class PrincipalParticipantView(APIView):
    permission_classes = [IsAuthenticatedSessionOrApiToken]
    parser_classes = [ClosedJSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, (ValueError, PrincipalResolutionError, AuthorizationProviderBindingError)):
            return api_error_response(
                code="participation_denied", message="Participation denied", status_code=403, request=self.request
            )
        return super().handle_exception(exc)


class PrincipalParticipantAdmissionView(PrincipalParticipantView):
    """Grant event participation to an existing service, never a synthetic user."""

    @extend_schema(request=AdmitPrincipalParticipantSerializer, responses={201: PrincipalParticipantSerializer})
    def post(self, request, event_id):
        command = AdmitPrincipalParticipantSerializer(data=request.data)
        command.is_valid(raise_exception=True)
        participant = services.admit_service_participant(
            authenticated_credential(request),
            event_id,
            PrincipalRef(command.validated_data["principal_uuid"], "service"),
            name=command.validated_data["name"],
        )
        return Response(
            PrincipalParticipantSerializer(
                {
                    "participant_uuid": participant.pk,
                    "principal_uuid": participant.principal_uuid,
                    "event_uuid": participant.event_id,
                }
            ).data,
            status=201,
        )


class PrincipalParticipantCurrentEventView(PrincipalParticipantView):
    """Use the ordinary participant-safe projection with an explicit event target."""

    @extend_schema(responses=ParticipantCurrentEventSerializer)
    def get(self, request, event_id):
        participant = services.participant_for_credential(authenticated_credential(request), event_id)
        return Response(ParticipantCurrentEventSerializer(projections.participant_current_event(participant)).data)
