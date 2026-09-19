"""Session-only communication inbox; reads never mutate participant receipts."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf.api._base import ctf_error_response
from ctf.api.communication_schema import CommunicationSchema
from ctf.api.organizer.communication import validate_query
from ctf.api.serializers.communication import (
    CommunicationEmptySerializer,
    CommunicationInboxItemSerializer,
    CommunicationJSONParser,
)
from ctf.exceptions import CTFCommunicationError
from ctf.services.communication.inbox import inbox_for_participant, interact_with_receipt
from shared.api.closed_serializer import ClosedSerializer
from shared.api.permissions import IsAuthenticatedSession


class InboxQuerySerializer(ClosedSerializer):
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)
    offset = serializers.IntegerField(min_value=0, max_value=100000, default=0)


class InboxPageSerializer(serializers.Serializer):
    results = CommunicationInboxItemSerializer(many=True)
    next_offset = serializers.IntegerField(allow_null=True)


class InboxView(APIView):
    schema = CommunicationSchema()
    permission_classes = [IsAuthenticatedSession]
    parser_classes = [CommunicationJSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, CTFCommunicationError):
            return ctf_error_response(self.request, exc)
        return super().handle_exception(exc)


class CommunicationInboxView(InboxView):
    @extend_schema(
        operation_id="ctf_communication_inbox_list",
        parameters=[InboxQuerySerializer],
        responses=InboxPageSerializer,
        auth=[{"cookieAuth": []}],
    )
    def get(self, request, event_id):
        query = validate_query(request, InboxQuerySerializer)
        start, limit = query["offset"], query["limit"]
        rows = list(inbox_for_participant(request.user, event_id)[start : start + limit + 1])
        return Response(
            {
                "results": CommunicationInboxItemSerializer(rows[:limit], many=True).data,
                "next_offset": start + limit if len(rows) > limit else None,
            }
        )


class CommunicationInboxDetailView(InboxView):
    @extend_schema(responses=CommunicationInboxItemSerializer, auth=[{"cookieAuth": []}])
    def get(self, request, event_id, snapshot_id):
        validate_query(request, CommunicationEmptySerializer)
        snapshot = inbox_for_participant(request.user, event_id).filter(pk=snapshot_id).first()
        if snapshot is None:
            raise CTFCommunicationError("Unavailable", code="CTF_COMMUNICATION_NOT_FOUND")
        return Response(CommunicationInboxItemSerializer(snapshot).data)


class CommunicationReadView(InboxView):
    acknowledge = False

    @extend_schema(
        request=CommunicationEmptySerializer, responses=CommunicationInboxItemSerializer, auth=[{"cookieAuth": []}]
    )
    def post(self, request, event_id, snapshot_id):
        validate_query(request, CommunicationEmptySerializer)
        serializer = CommunicationEmptySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snapshot = interact_with_receipt(request.user, event_id, snapshot_id, acknowledge=self.acknowledge)
        return Response(CommunicationInboxItemSerializer(snapshot).data)


class CommunicationAcknowledgeView(CommunicationReadView):
    acknowledge = True
