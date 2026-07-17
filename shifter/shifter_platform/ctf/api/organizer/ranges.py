"""Range lifecycle views for the canonical CTF API.

Participant range status/access plus the organizer range operations
(bulk provisioning, spare pool, per-participant lifecycle actions, recovery).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf.api._base import CTF_ORGANIZER_PERMISSIONS, CTF_PARTICIPANT_PERMISSIONS, _CtfApiError
from ctf.api.organizer._base import (
    _EVENT_NOT_FOUND,
    _EVENT_READ,
    _EVENT_WRITE,
    _MAX_SPARE_POOL_COUNT,
    _PARTICIPANT_NOT_FOUND,
    _PLAY_READ,
    _RANGE_REQUEST_FAILED,
    _RECOVERY_REQUEST_FAILED,
    _SPARE_POOL_REQUEST_FAILED,
    _actor,
    _raise_bad_request,
    _raise_not_found,
    _resolve_active_participant,
    _resolve_owned_event,
    _resolve_owned_participant,
)
from ctf.api.serializers import (
    ParticipantRangeActionResultSerializer,
    RangeAccessResponseSerializer,
    RangeListResponseSerializer,
    RangeProvisionQueuedSerializer,
    RangeRecoveryRequestSerializer,
    RangeRecoveryResultSerializer,
    RangeStatusResponseSerializer,
    SparePoolRequestSerializer,
    SpareProvisionResultSerializer,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID


def _parse_spare_range_instance_id(body: dict[str, object]) -> int | None:
    """Validate the optional spare-range field as a positive int, else raise ``_BodyParseError``.

    Boundary-only: the recovery service still validates ``strategy`` against
    ``ctf.enums`` and resolves/validates the spare range itself. Mirrors
    ``ctf.views.api.ranges._parse_spare_range_instance_id``.
    """
    from ctf.views._parsing import _BodyParseError

    if "spare_range_instance_id" not in body or body["spare_range_instance_id"] is None:
        return None
    value = body["spare_range_instance_id"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _BodyParseError("spare_range_instance_id must be a positive integer")
    return value


def _parse_spare_pool_count(body: dict[str, object]) -> int:
    """Validate the required ``count`` field as a bounded non-negative int, else raise ``_BodyParseError``.

    Mirrors ``ctf.views.api.ranges._parse_spare_pool_count``.
    """
    from ctf.views._parsing import _BodyParseError

    if "count" not in body:
        raise _BodyParseError("count is required")
    value = body["count"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _BodyParseError("count must be an integer")
    if value < 0:
        raise _BodyParseError("count must be non-negative")
    if value > _MAX_SPARE_POOL_COUNT:
        raise _BodyParseError(f"count must not exceed {_MAX_SPARE_POOL_COUNT}")
    return value


def _run_participant_range_action(
    request: Request, participant_id: UUID, action_fn: Callable[[UUID], object]
) -> Response:
    """Resolve+own the participant, run a range action, and map range errors to 400.

    Mirrors ``ctf.views.api.ranges._participant_range_action`` +
    ``_run_participant_range_action``: 404 for an unknown participant, 403 when the
    actor does not own the event, and 400 for ``CTFNotFoundError``/``CTFRangeError``
    raised by the action itself.
    """
    from ctf.exceptions import CTFNotFoundError, CTFRangeError

    try:
        _resolve_owned_participant(request, participant_id)
        try:
            result = action_fn(participant_id)
        except (CTFNotFoundError, CTFRangeError):
            _raise_bad_request(_RANGE_REQUEST_FAILED)
        return Response(result)
    except _CtfApiError as exc:
        return exc.to_response(request)


class ParticipantRangeStatusView(APIView):
    """Return the range status for the requesting participant's active event."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=RangeStatusResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return the participant's range status, or the not-assigned sentinel."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services import range as range_service

        try:
            participant = _resolve_active_participant(request)
            if participant is None:
                return Response({"status": "not_assigned", "range_instance_id": None})
            try:
                result = range_service.get_range_status(participant.pk)
            except CTFNotFoundError:
                _raise_not_found(_PARTICIPANT_NOT_FOUND)
            return Response(result)
        except _CtfApiError as exc:
            return exc.to_response(request)


class ParticipantRangeAccessView(APIView):
    """Point participants at the mission_control Guacamole RDP access flow."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ
    required_write_scopes = _PLAY_READ

    @extend_schema(request=None, responses=RangeAccessResponseSerializer)
    def post(self, request: Request) -> Response:
        """Return the mission_control RDP endpoint redirect (participants are standard users)."""
        from django.urls import reverse

        return Response(
            {
                "redirect": reverse("v1:mission_control:guacamole-rdp-url"),
                "message": "Use the mission_control RDP endpoint directly.",
            }
        )


class EventRangeListView(APIView):
    """Range status for all participants in an owned event, plus provision progress."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ

    @extend_schema(responses=RangeListResponseSerializer)
    def get(self, request: Request, event_id: UUID) -> Response:
        """Return per-participant range rows and the provisioning-progress projection."""
        from ctf.models import CTFParticipant
        from ctf.services import range as range_service

        try:
            event = _resolve_owned_event(request, event_id)
        except _CtfApiError as exc:
            return exc.to_response(request)
        participants = CTFParticipant.objects.filter(event=event).order_by("name")
        data = [
            {
                "participant_id": str(p.pk),
                "name": p.name,
                "email": p.email,
                "range_instance_id": p.range_instance_id,
                "range_status": p.range_status or "not_assigned",
            }
            for p in participants
        ]
        progress = range_service.get_provision_progress(event_id)
        return Response({"event_id": str(event_id), "ranges": data, "progress": progress})


class EventRangeProvisionView(APIView):
    """Queue bulk range provisioning for an owned event."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses={202: RangeProvisionQueuedSerializer})
    def post(self, request: Request, event_id: UUID) -> Response:
        """Enqueue (or coalesce onto) a background spin-up task and return 202 immediately."""
        from ctf.services import range as range_service

        try:
            _resolve_owned_event(request, event_id)
        except _CtfApiError as exc:
            return exc.to_response(request)
        task = range_service.request_event_provisioning(event_id, source="manual")
        return Response(
            {
                "event_id": str(event_id),
                "status": "queued",
                "task_id": str(task.pk),
                "task_status": task.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class EventSpareProvisionView(APIView):
    """Set/top-up an owned event's spare-range recovery pool (issue #1018)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=SparePoolRequestSerializer, responses=SpareProvisionResultSerializer)
    def post(self, request: Request, event_id: UUID) -> Response:
        """Enforce ownership, then parse ``count`` and top the spare pool up to it."""
        try:
            _resolve_owned_event(request, event_id)
            return self._provision_spares(request, event_id)
        except _CtfApiError as exc:
            return exc.to_response(request)

    @staticmethod
    def _provision_spares(request: Request, event_id: UUID) -> Response:
        """Parse the spare-pool body, invoke the service, and map errors to the shared envelope."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services.range import provision_event_spares
        from ctf.views._parsing import _BodyParseError

        body = request.data if isinstance(request.data, dict) else None
        try:
            if body is None:
                raise _BodyParseError("Request body must be a JSON object")
            count = _parse_spare_pool_count(body)
        except _BodyParseError:
            _raise_bad_request(_SPARE_POOL_REQUEST_FAILED)
        try:
            result = provision_event_spares(event_id, count, operator=_actor(request))
        except CTFNotFoundError:
            _raise_not_found(_EVENT_NOT_FOUND)
        return Response(result)


class ParticipantRangeProvisionView(APIView):
    """Provision a range for a single participant (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ParticipantRangeActionResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Provision the participant's range via the range service."""
        from ctf.services import range as range_service

        return _run_participant_range_action(request, participant_id, range_service.provision_participant_range)


class ParticipantRangeDestroyView(APIView):
    """Destroy a range for a single participant (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ParticipantRangeActionResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Destroy the participant's range via the range service."""
        from ctf.services import range as range_service

        return _run_participant_range_action(request, participant_id, range_service.destroy_participant_range)


class ParticipantRangeStopView(APIView):
    """Stop (pause) a participant's range (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ParticipantRangeActionResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Stop the participant's range via the range service."""
        from ctf.services import range as range_service

        return _run_participant_range_action(request, participant_id, range_service.stop_participant_range)


class ParticipantRangeStartView(APIView):
    """Start (resume) a participant's stopped range (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ParticipantRangeActionResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Start the participant's range via the range service."""
        from ctf.services import range as range_service

        return _run_participant_range_action(request, participant_id, range_service.start_participant_range)


class ParticipantRangeRestartView(APIView):
    """Restart a participant's range (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ParticipantRangeActionResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Restart the participant's range via the range service."""
        from ctf.services import range as range_service

        return _run_participant_range_action(request, participant_id, range_service.restart_participant_range)


class ParticipantRangeRecoverView(APIView):
    """Recover a participant's range that is beyond in-place repair (issue #1018).

    Organizer-only; a participant may not recover their own or anyone's range.
    Only ``strategy`` and the optional ``spare_range_instance_id`` are read; the
    old range is always destroyed (no disposition/forensics-retention choice).
    """

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=RangeRecoveryRequestSerializer, responses=RangeRecoveryResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Enforce ownership, then parse the recovery body and run the recovery service."""
        try:
            _resolve_owned_participant(request, participant_id)
            return self._recover(request, participant_id)
        except _CtfApiError as exc:
            return exc.to_response(request)

    @staticmethod
    def _recover(request: Request, participant_id: UUID) -> Response:
        """Parse the recovery body, invoke the service, and map errors to the shared envelope."""
        from ctf.exceptions import CTFNotFoundError, CTFRangeError, CTFValidationError
        from ctf.services.range import recover_participant_range
        from ctf.views._parsing import _BodyParseError, _get_body_str

        body = request.data if isinstance(request.data, dict) else None
        try:
            if body is None:
                raise _BodyParseError("Request body must be a JSON object")
            strategy = _get_body_str(body, "strategy", required=True)
            spare_range_instance_id = _parse_spare_range_instance_id(body)
            result = recover_participant_range(
                participant_id,
                strategy=strategy,
                operator=_actor(request),
                spare_range_instance_id=spare_range_instance_id,
            )
        except _BodyParseError:
            _raise_bad_request(_RECOVERY_REQUEST_FAILED)
        except CTFNotFoundError:
            _raise_not_found(_PARTICIPANT_NOT_FOUND)
        except (CTFValidationError, CTFRangeError):
            _raise_bad_request(_RECOVERY_REQUEST_FAILED)
        return Response(result)
