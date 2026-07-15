"""Canonical DRF organizer views for the CTF API.

Proper DRF replacements for the transitional ``legacy_api_view`` wrappers: each
view validates a request serializer, calls the authoritative ``ctf.services.*``
facade, and returns a typed response serializer, so the ``/api/v1/ctf/`` surface
carries real generated OpenAPI types for the SPA. Domain correctness
(validation, ownership, state machine, range teardown) stays in the service
layer; these views own only HTTP shape, permission/scope enforcement, and
per-event ownership resolution.

Service and bridge calls are imported lazily inside the methods so the existing
``patch("ctf.services...")`` / ``patch("ctf.bridges...")`` test seams continue to
intercept them at call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf.api._base import CTF_ORGANIZER_PERMISSIONS, ctf_actor_user
from ctf.api.serializers import (
    EventDetailSerializer,
    EventListResponseSerializer,
    EventMutationResultSerializer,
    EventSummarySerializer,
    EventWriteSerializer,
    ForceDeleteEventRequestSerializer,
    ForceDeleteEventResultSerializer,
    ScenarioListResponseSerializer,
)
from shared.api.errors import api_error_response
from shared.api_tokens import scopes

if TYPE_CHECKING:
    from uuid import UUID

    from django.contrib.auth.models import User

    from ctf.models import CTFEvent

_EVENT_READ = (scopes.CTF_EVENT_READ,)
_EVENT_WRITE = (scopes.CTF_EVENT_WRITE,)
_INVALID_EVENT = "Invalid event request."
_EVENT_NOT_FOUND = "Event not found"
_FORBIDDEN = "Forbidden"


def _actor(request: Request) -> User:
    """Return the organizer actor (guaranteed non-None after permissions)."""
    actor = ctf_actor_user(request)
    if actor is None:  # pragma: no cover - permission classes already admitted the request
        raise AssertionError("CTF organizer actor unavailable after permission check")
    return actor


def _resolve_owned_event(request: Request, event_id: UUID) -> tuple[CTFEvent | None, Response | None]:
    """Resolve an event and enforce ownership; return ``(event, error)``.

    Mirrors ``ctf.views.api._common._resolve_owned_event_json``: 404 when the
    event does not exist, 403 when the actor does not own it.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        return None, api_error_response(
            code="not_found", message=_EVENT_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND, request=request
        )
    if event.created_by_id != _actor(request).pk:
        return None, api_error_response(
            code="permission_denied", message=_FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN, request=request
        )
    return event, None


def _invalid_event(request: Request) -> Response:
    """Return the shared 400 envelope for an invalid event request."""
    return api_error_response(
        code="invalid", message=_INVALID_EVENT, status_code=status.HTTP_400_BAD_REQUEST, request=request
    )


class EventListView(APIView):
    """List the organizer's events (GET) or create one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=EventListResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return the organizer's events."""
        from ctf.services import get_organizer_events

        events = get_organizer_events(_actor(request))
        return Response({"events": EventSummarySerializer(events, many=True).data})

    @extend_schema(request=EventWriteSerializer, responses={201: EventMutationResultSerializer})
    def post(self, request: Request) -> Response:
        """Create an event from the request body."""
        from django.core.exceptions import ValidationError

        from ctf.exceptions import CTFValidationError
        from ctf.services import create_event

        serializer = EventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event = create_event(_actor(request), dict(serializer.validated_data))
        except (CTFValidationError, ValidationError):
            return _invalid_event(request)
        return Response(
            {"id": str(event.id), "name": event.name, "status": event.status},
            status=status.HTTP_201_CREATED,
        )


class EventDetailView(APIView):
    """Get, update, or delete a single owned event."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=EventDetailSerializer)
    def get(self, request: Request, event_id: UUID) -> Response:
        """Return the full event detail projection."""
        event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        assert event is not None
        return Response(EventDetailSerializer(event).data)

    @extend_schema(request=EventWriteSerializer, responses=EventMutationResultSerializer)
    def put(self, request: Request, event_id: UUID) -> Response:
        """Update mutable fields of an owned event."""
        from django.core.exceptions import ValidationError

        from ctf.exceptions import CTFStateError, CTFValidationError
        from ctf.services import update_event

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        serializer = EventWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_event(event_id, dict(serializer.validated_data))
        except (CTFValidationError, CTFStateError, ValidationError):
            return _invalid_event(request)
        return Response({"id": str(updated.id), "name": updated.name, "status": updated.status})

    @extend_schema(responses={204: None})
    def delete(self, request: Request, event_id: UUID) -> Response:
        """Soft-delete an owned event."""
        from ctf.services import delete_event

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        delete_event(event_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ForceDeleteEventView(APIView):
    """Force-delete an event and tear down its ranges."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=ForceDeleteEventRequestSerializer, responses=ForceDeleteEventResultSerializer)
    def post(self, request: Request, event_id: UUID) -> Response:
        """Validate the confirmation name and force-delete the event."""
        from ctf.exceptions import CTFValidationError
        from ctf.models import CTFEvent

        try:
            event = CTFEvent.all_objects.get(pk=event_id)
        except CTFEvent.DoesNotExist:
            return api_error_response(
                code="not_found", message=_EVENT_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND, request=request
            )
        if event.created_by_id != _actor(request).pk:
            return api_error_response(
                code="permission_denied", message=_FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN, request=request
            )
        confirmation_name = request.data.get("confirmation_name") if isinstance(request.data, dict) else None
        if not confirmation_name:
            return api_error_response(
                code="invalid",
                message="confirmation_name is required.",
                status_code=status.HTTP_400_BAD_REQUEST,
                request=request,
            )
        import ctf.services as ctf_services

        try:
            result = ctf_services.force_delete_event(event_id, _actor(request), confirmation_name)
        except CTFValidationError:
            return _invalid_event(request)
        return Response(result)


class ScenarioListView(APIView):
    """List CMS scenarios available for CTF events."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ

    @extend_schema(responses=ScenarioListResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return scenario id/name pairs from the CMS registry."""
        import ctf.bridges as ctf_bridges

        scenarios = [{"id": sid, "name": name} for sid, name in ctf_bridges.cms_list_scenarios(_actor(request))]
        return Response({"scenarios": scenarios})
