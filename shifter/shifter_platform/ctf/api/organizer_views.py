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

import logging
from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf.api._base import (
    CTF_ORGANIZER_PERMISSIONS,
    CTF_PARTICIPANT_PERMISSIONS,
    CTF_ROLE_PERMISSIONS,
    ctf_actor_user,
)
from ctf.api.serializers import (
    AssignBracketRequestSerializer,
    AssignBracketResultSerializer,
    ChallengeFileListResponseSerializer,
    ChallengeFileUploadResultSerializer,
    ChallengeFileUploadSerializer,
    ChallengeHintSerializer,
    ChallengeListResponseSerializer,
    ChallengeMutationResultSerializer,
    ChallengeWriteSerializer,
    CtfScenarioListResponseSerializer,
    DeleteSuccessSerializer,
    EmailTemplateResponseSerializer,
    EmailTemplateRevertResultSerializer,
    EmailTemplateWriteSerializer,
    EventDetailSerializer,
    EventListResponseSerializer,
    EventMutationResultSerializer,
    EventSummarySerializer,
    EventWriteSerializer,
    FileDownloadResponseSerializer,
    FlagCreateResultSerializer,
    FlagWriteSerializer,
    ForceDeleteEventRequestSerializer,
    ForceDeleteEventResultSerializer,
    HintListResponseSerializer,
    HintWriteSerializer,
    NotificationAnnounceRequestSerializer,
    NotificationAnnounceResultSerializer,
    NotificationListResponseSerializer,
    NotificationSendResultSerializer,
    OrganizerChallengeDetailSerializer,
    ParticipantDeleteResultSerializer,
    ParticipantDetailSerializer,
    ParticipantImportResultSerializer,
    ParticipantImportSerializer,
    ParticipantInviteResultSerializer,
    ParticipantInviteSerializer,
    ParticipantListResponseSerializer,
    ParticipantRangeActionResultSerializer,
    PrerequisiteCreateResultSerializer,
    PrerequisiteListResponseSerializer,
    PrerequisiteWriteSerializer,
    RangeAccessResponseSerializer,
    RangeListResponseSerializer,
    RangeProvisionQueuedSerializer,
    RangeRecoveryRequestSerializer,
    RangeRecoveryResultSerializer,
    RangeStatusResponseSerializer,
    RateChallengeRequestSerializer,
    RateChallengeResultSerializer,
    ResendInviteResultSerializer,
    ScoreTimelineResponseSerializer,
    SendInvitationsResultSerializer,
    SparePoolRequestSerializer,
    SpareProvisionResultSerializer,
    SubmissionListResponseSerializer,
    SubmitFlagRequestSerializer,
    SubmitFlagResultSerializer,
    UseHintRequestSerializer,
    UseHintResultSerializer,
)
from shared.api.errors import api_error_response
from shared.api_tokens import scopes
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from django.contrib.auth.models import User

    from ctf.models import CTFChallenge, CTFChallengeFile, CTFEvent, CTFNotification, CTFParticipant

_EVENT_READ = (scopes.CTF_EVENT_READ,)
_EVENT_WRITE = (scopes.CTF_EVENT_WRITE,)
_PLAY_READ = (scopes.CTF_PLAY_READ,)
_PLAY_WRITE = (scopes.CTF_PLAY_WRITE,)
_EVENT_OR_PLAY_READ = (scopes.CTF_EVENT_READ, scopes.CTF_PLAY_READ)
_INVALID_EVENT = "Invalid event request."
_EVENT_NOT_FOUND = "Event not found"
_FORBIDDEN = "Forbidden"
_CHALLENGE_NOT_FOUND = "Challenge not found"
_INVALID_CHALLENGE = "Invalid challenge request."
_CHALLENGE_ACTION_FAILED = "Could not process challenge action."
_CHALLENGE_OR_PARTICIPANT_NOT_FOUND = "Challenge or participant not found."
_NO_MORE_HINTS = "No more hints available"
_PARTICIPANT_NOT_FOUND = "Participant not found"
_INVALID_PARTICIPANT_REQUEST = "Invalid participant request."
_BRACKET_NOT_FOUND = "Bracket not found"
_RANGE_REQUEST_FAILED = "Could not process range request."
_RECOVERY_REQUEST_FAILED = "Could not process range recovery request."
_SPARE_POOL_REQUEST_FAILED = "Could not process spare pool request."
_NOTIFICATION_NOT_FOUND = "Notification not found"
_INVALID_NOTIFICATION = "Invalid notification request."
# Sane operator-facing upper bound on a single spare-pool top-up request: large
# enough for any real event's recovery pool, small enough to block a
# fat-fingered or malicious request from queuing unbounded provisioning work.
_MAX_SPARE_POOL_COUNT = 25

logger = logging.getLogger(__name__)


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


def _forbidden(request: Request) -> Response:
    """Return the shared 403 envelope with the legacy ``Forbidden`` message."""
    return api_error_response(
        code="permission_denied", message=_FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN, request=request
    )


def _not_found(request: Request, message: str) -> Response:
    """Return the shared 404 envelope carrying a controlled message."""
    return api_error_response(code="not_found", message=message, status_code=status.HTTP_404_NOT_FOUND, request=request)


def _bad_request(request: Request, message: str) -> Response:
    """Return the shared 400 envelope carrying a controlled message."""
    return api_error_response(code="invalid", message=message, status_code=status.HTTP_400_BAD_REQUEST, request=request)


def _resolve_owned_challenge(request: Request, challenge_id: UUID) -> tuple[CTFChallenge | None, Response | None]:
    """Resolve a challenge and enforce event ownership; return ``(challenge, error)``.

    Mirrors ``ctf.views.api._common._resolve_owned_challenge_json``: 404 when the
    challenge does not exist, 403 when the actor does not own its event.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_challenge

    try:
        challenge = get_challenge(challenge_id)
    except CTFNotFoundError:
        return None, _not_found(request, _CHALLENGE_NOT_FOUND)
    if challenge.event.created_by_id != _actor(request).pk:
        return None, _forbidden(request)
    return challenge, None


def _resolve_owned_participant(request: Request, participant_id: UUID) -> tuple[CTFParticipant | None, Response | None]:
    """Resolve a participant and enforce event ownership; return ``(participant, error)``.

    Mirrors ``ctf.views._access._resolve_owned_participant``: 404 when the
    participant does not exist, 403 when the actor does not own its event.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_participant

    try:
        participant = get_participant(participant_id)
    except CTFNotFoundError:
        return None, _not_found(request, _PARTICIPANT_NOT_FOUND)
    if participant.event.created_by_id != _actor(request).pk:
        return None, _forbidden(request)
    return participant, None


def _delete_via_service(request: Request, action_fn: object, target_id: UUID) -> Response:
    """Run a delete-style service action, returning ``{"success": True}`` or a mapped error.

    Mirrors ``ctf.views.api._common._delete_via_service_response``:
    ``CTFPermissionError`` -> 403, ``CTFNotFoundError`` -> 404,
    ``CTFStateError`` -> 400.
    """
    from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError

    try:
        action_fn(target_id, actor_id=_actor(request).pk)  # type: ignore[operator]
    except CTFPermissionError:
        return _forbidden(request)
    except CTFNotFoundError:
        return _not_found(request, "Resource not found.")
    except CTFStateError:
        return _bad_request(request, "Invalid request.")
    return Response({"success": True})


def _resolve_challenge_participant(
    request: Request, challenge_id: UUID
) -> tuple[CTFParticipant | None, Response | None]:
    """Resolve a challenge (404) then the actor's participant scoped to its event (403).

    Mirrors ``ctf.views.api.play._resolve_challenge_participant``.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.services.challenge import get_challenge
    from ctf.services.participant import get_participant_by_user

    try:
        challenge = get_challenge(challenge_id)
    except CTFNotFoundError:
        return None, _not_found(request, _CHALLENGE_NOT_FOUND)
    participant = get_participant_by_user(_actor(request), event_id=challenge.event_id)
    if not participant:
        return None, _forbidden(request)
    return participant, None


def _resolve_active_participant(request: Request) -> CTFParticipant | None:
    """Resolve the participant for the actor's active event, or ``None``.

    Mirrors ``ctf.views._access._get_active_participant``: the participant is
    scoped to ``get_user_role(actor).active_ctf_event`` rather than an unscoped
    first-row pick, so a user enrolled in several events acts as the right one.
    """
    from ctf.bridges import get_user_role
    from ctf.services.participant import get_participant_by_user

    actor = ctf_actor_user(request)
    if actor is None:
        return None
    role = get_user_role(actor)
    if role.active_ctf_event is None:
        return None
    return get_participant_by_user(actor, event_id=role.active_ctf_event.id)


def _is_file_download_allowed(request: Request, challenge_file: CTFChallengeFile) -> bool:
    """Return True if the actor may download this challenge file.

    Mirrors ``ctf.views.api.files._is_file_download_allowed``: organizer-owners
    get full access; otherwise the actor must be a non-disqualified participant
    of the event AND the challenge must be available to them.
    """
    from ctf.bridges import get_user_role
    from ctf.exceptions import CTFStateError, CTFValidationError
    from ctf.services.challenge import assert_challenge_available_for_participant
    from ctf.services.participant import get_participant_by_user, is_active_participant

    user = _actor(request)
    event = challenge_file.challenge.event
    role = get_user_role(user)
    if role.is_ctf_organizer and event.created_by_id == user.pk:
        return True
    if not is_active_participant(user, event=event):
        return False
    participant = get_participant_by_user(user, event_id=event.id)
    allowed = participant is not None
    if participant is not None:
        try:
            assert_challenge_available_for_participant(participant, challenge_file.challenge)
        except (CTFStateError, CTFValidationError):
            allowed = False
    return allowed


class EventListView(APIView):
    """List the organizer's events (GET) or create one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(operation_id="ctf_events_list", responses=EventListResponseSerializer)
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

    @extend_schema(operation_id="ctf_events_retrieve", responses=EventDetailSerializer)
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

    @extend_schema(responses=CtfScenarioListResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return scenario id/name pairs from the CMS registry."""
        import ctf.bridges as ctf_bridges

        scenarios = [{"id": sid, "name": name} for sid, name in ctf_bridges.cms_list_scenarios(_actor(request))]
        return Response({"scenarios": scenarios})


def _challenge_detail_payload(challenge: CTFChallenge) -> dict[str, object]:
    """Render the organizer GET-challenge JSON payload.

    Mirrors ``ctf.views.api.challenges._challenge_detail_payload`` key-for-key.
    """
    return {
        "id": str(challenge.id),
        "name": challenge.name,
        "description": challenge.description,
        "category": challenge.category,
        "points": challenge.points,
        "difficulty": challenge.difficulty,
        "flag_format": challenge.flag_format,
        "hints": [
            {"id": str(h.id), "text": h.text, "penalty": h.penalty, "order": h.order} for h in challenge.hints.all()
        ],
        "max_attempts": challenge.max_attempts,
        "order": challenge.order,
        "release_time": challenge.release_time.isoformat() if challenge.release_time else None,
        "tags": list(challenge.tags.values_list("name", flat=True)),
        "topics": list(challenge.topics.values_list("name", flat=True)),
        "solution": challenge.solution,
    }


class ChallengeListView(APIView):
    """List an event's challenges (GET) or create one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=ChallengeListResponseSerializer)
    def get(self, request: Request, event_id: UUID) -> Response:
        """Return the challenges belonging to an owned event."""
        from ctf.exceptions import CTFPermissionError
        from ctf.services import list_challenges_for_event

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        try:
            challenges = list_challenges_for_event(event_id, actor_id=_actor(request).pk).prefetch_related(
                "tags", "topics"
            )
        except CTFPermissionError:
            return _forbidden(request)
        data = [
            {
                "id": str(c.id),
                "name": c.name,
                "category": c.category,
                "points": c.points,
                "difficulty": c.difficulty,
                "order": c.order,
                "tags": list(c.tags.values_list("name", flat=True)),
                "topics": list(c.topics.values_list("name", flat=True)),
            }
            for c in challenges
        ]
        return Response({"challenges": data})

    @extend_schema(request=ChallengeWriteSerializer, responses={201: ChallengeMutationResultSerializer})
    def post(self, request: Request, event_id: UUID) -> Response:
        """Create a challenge under an owned event."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services import create_challenge

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        serializer = ChallengeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            challenge = create_challenge(event_id, dict(serializer.validated_data), actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except CTFNotFoundError:
            return _not_found(request, "Challenge not found.")
        except (CTFValidationError, CTFStateError):
            return _bad_request(request, _INVALID_CHALLENGE)
        return Response(
            {
                "id": str(challenge.id),
                "name": challenge.name,
                "category": challenge.category,
                "points": challenge.points,
            },
            status=status.HTTP_201_CREATED,
        )


class ChallengeDetailView(APIView):
    """Get, update, or delete a single owned challenge."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=OrganizerChallengeDetailSerializer)
    def get(self, request: Request, challenge_id: UUID) -> Response:
        """Return the full organizer challenge detail projection."""
        challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        assert challenge is not None
        return Response(_challenge_detail_payload(challenge))

    @extend_schema(request=ChallengeWriteSerializer, responses=ChallengeMutationResultSerializer)
    def put(self, request: Request, challenge_id: UUID) -> Response:
        """Update mutable fields of an owned challenge."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services import update_challenge

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        serializer = ChallengeWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_challenge(challenge_id, dict(serializer.validated_data), actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except (CTFNotFoundError, CTFValidationError, CTFStateError):
            return _bad_request(request, _INVALID_CHALLENGE)
        return Response(
            {
                "id": str(updated.id),
                "name": updated.name,
                "category": updated.category,
                "points": updated.points,
            }
        )

    @extend_schema(responses={204: None})
    def delete(self, request: Request, challenge_id: UUID) -> Response:
        """Delete an owned challenge."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError
        from ctf.services import delete_challenge

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        try:
            delete_challenge(challenge_id, actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except (CTFNotFoundError, CTFStateError):
            return _bad_request(request, _INVALID_CHALLENGE)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddFlagView(APIView):
    """Add a flag to an owned challenge."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=FlagWriteSerializer, responses={201: FlagCreateResultSerializer})
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Validate the flag body and create the flag record."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services.challenge import add_flag

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        serializer = FlagWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        flag_value = data["flag"].strip()
        flag_type = data["flag_type"]
        # Flag value is only required for static and regex types.
        if flag_type in ("static", "regex") and not flag_value:
            return _bad_request(request, "Invalid flag request.")
        flag_data = {
            "flag": flag_value,
            "flag_type": flag_type,
            "case_sensitive": data["case_sensitive"],
            "order": data["order"],
            "validator_config": data["validator_config"],
        }
        try:
            flag_obj = add_flag(challenge_id, flag_data, actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except CTFNotFoundError:
            return _not_found(request, "Flag or challenge not found.")
        except (CTFStateError, CTFValidationError):
            return _bad_request(request, "Invalid flag request.")
        response_data: dict[str, object] = {
            "id": str(flag_obj.id),
            "flag_type": flag_obj.flag_type,
            "case_sensitive": flag_obj.case_sensitive,
            "order": flag_obj.order,
        }
        if flag_obj.validator_config:
            response_data["validator_config"] = flag_obj.validator_config
        return Response(response_data, status=status.HTTP_201_CREATED)


class RemoveFlagView(APIView):
    """Remove a flag from an owned challenge."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=DeleteSuccessSerializer)
    def post(self, request: Request, flag_id: UUID) -> Response:
        """Resolve the flag, enforce ownership, and delete via the service."""
        from ctf.models import CTFFlag
        from ctf.services.challenge import remove_flag

        try:
            flag_obj = CTFFlag.objects.select_related("challenge__event").get(pk=flag_id)
        except CTFFlag.DoesNotExist:
            return _not_found(request, "Flag not found")
        if flag_obj.challenge.event.created_by_id != _actor(request).pk:
            return _forbidden(request)
        return _delete_via_service(request, remove_flag, flag_id)


class ChallengeHintsView(APIView):
    """List an owned challenge's hints (GET) or add one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=HintListResponseSerializer)
    def get(self, request: Request, challenge_id: UUID) -> Response:
        """Return the hints for an owned challenge."""
        from ctf.services.hint import get_hints

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        data = [
            {"id": str(h.id), "text": h.text, "penalty": h.penalty, "order": h.order} for h in get_hints(challenge_id)
        ]
        return Response({"hints": data})

    @extend_schema(request=HintWriteSerializer, responses={201: ChallengeHintSerializer})
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Add a hint to an owned challenge."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services.hint import add_hint

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        serializer = HintWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            hint = add_hint(challenge_id, dict(serializer.validated_data), actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except (CTFNotFoundError, CTFStateError, CTFValidationError):
            return _bad_request(request, "Could not process hint request.")
        return Response(
            {"id": str(hint.id), "text": hint.text, "penalty": hint.penalty, "order": hint.order},
            status=status.HTTP_201_CREATED,
        )


class HintDeleteView(APIView):
    """Delete a hint from an owned challenge."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request, hint_id: UUID) -> Response:
        """Delete a hint, mapping service exceptions to the shared envelope."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError
        from ctf.services.hint import remove_hint

        try:
            remove_hint(hint_id, actor_id=_actor(request).pk)
        except CTFPermissionError:
            return _forbidden(request)
        except CTFNotFoundError:
            return _not_found(request, "Hint or challenge not found.")
        except CTFStateError:
            return _bad_request(request, "Could not process hint request.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChallengeFilesView(APIView):
    """List an owned challenge's files (GET) or upload one (POST, multipart)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    parser_classes = [MultiPartParser, FormParser]
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=ChallengeFileListResponseSerializer)
    def get(self, request: Request, challenge_id: UUID) -> Response:
        """Return the file attachments for an owned challenge."""
        from ctf.services.attachment import get_challenge_files

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        data = [
            {
                "id": str(f.id),
                "filename": f.filename,
                "display_name": f.display_name,
                "file_size_bytes": f.file_size_bytes,
                "file_size_display": f.file_size_display,
                "content_type": f.content_type,
                "sha256_hash": f.sha256_hash,
                "order": f.order,
                "created_at": f.created_at.isoformat(),
            }
            for f in get_challenge_files(challenge_id)
        ]
        return Response({"files": data})

    @extend_schema(request=ChallengeFileUploadSerializer, responses={201: ChallengeFileUploadResultSerializer})
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Upload a multipart file attachment to an owned challenge."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services.attachment import add_challenge_file

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return _bad_request(request, "No file provided")
        display_name = request.data.get("display_name", "")
        try:
            challenge_file = add_challenge_file(
                challenge_id=challenge_id,
                file_obj=uploaded_file,
                filename=uploaded_file.name or "unnamed",
                display_name=display_name,
                content_type=uploaded_file.content_type or "application/octet-stream",
                actor_id=_actor(request).pk,
            )
        except CTFPermissionError:
            return _forbidden(request)
        except CTFNotFoundError:
            return _not_found(request, "File or challenge not found.")
        except (CTFStateError, CTFValidationError):
            return _bad_request(request, "Invalid file request.")
        return Response(
            {
                "id": str(challenge_file.id),
                "filename": challenge_file.filename,
                "display_name": challenge_file.display_name,
                "file_size_bytes": challenge_file.file_size_bytes,
                "file_size_display": challenge_file.file_size_display,
            },
            status=status.HTTP_201_CREATED,
        )


class ChallengeFileDeleteView(APIView):
    """Delete a challenge file attachment."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=DeleteSuccessSerializer)
    def post(self, request: Request, file_id: UUID) -> Response:
        """Resolve the file, enforce ownership, and delete via the service."""
        from ctf.models import CTFChallengeFile
        from ctf.services.attachment import remove_challenge_file

        try:
            challenge_file = CTFChallengeFile.objects.select_related("challenge__event").get(pk=file_id)
        except CTFChallengeFile.DoesNotExist:
            return _not_found(request, "File not found")
        if challenge_file.challenge.event.created_by_id != _actor(request).pk:
            return _forbidden(request)
        return _delete_via_service(request, remove_challenge_file, file_id)


class FileDownloadView(APIView):
    """Return a presigned download URL for a challenge file (role-based access)."""

    permission_classes = CTF_ROLE_PERMISSIONS
    required_read_scopes = _EVENT_OR_PLAY_READ

    @extend_schema(responses=FileDownloadResponseSerializer)
    def get(self, request: Request, file_id: UUID) -> Response:
        """Resolve the file, apply the fine-grained access policy, and issue a URL."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.models import CTFChallengeFile
        from ctf.services.attachment import get_download_url

        try:
            challenge_file = CTFChallengeFile.objects.select_related("challenge__event").get(pk=file_id)
        except CTFChallengeFile.DoesNotExist:
            return _not_found(request, "File not found")
        if not _is_file_download_allowed(request, challenge_file):
            return _forbidden(request)
        try:
            url, filename = get_download_url(file_id)
        except CTFNotFoundError:
            return _not_found(request, "File or challenge not found.")
        return Response({"url": url, "filename": filename})


class ChallengePrerequisitesView(APIView):
    """List an owned challenge's prerequisites (GET) or add one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=PrerequisiteListResponseSerializer)
    def get(self, request: Request, challenge_id: UUID) -> Response:
        """Return the prerequisites for an owned challenge."""
        from ctf.services.challenge import get_prerequisites

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        data = [
            {
                "id": str(p.id),
                "required_challenge_id": str(p.required_challenge_id),
                "required_challenge_name": p.required_challenge.name,
                "required_challenge_category": p.required_challenge.category,
                "required_challenge_points": p.required_challenge.points,
            }
            for p in get_prerequisites(challenge_id)
        ]
        return Response({"prerequisites": data})

    @extend_schema(request=PrerequisiteWriteSerializer, responses={201: PrerequisiteCreateResultSerializer})
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Add a prerequisite to an owned challenge."""
        from ctf.exceptions import CTFNotFoundError, CTFPermissionError, CTFStateError, CTFValidationError
        from ctf.services.challenge import add_prerequisite

        _challenge, error = _resolve_owned_challenge(request, challenge_id)
        if error is not None:
            return error
        serializer = PrerequisiteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            prereq = add_prerequisite(
                challenge_id, serializer.validated_data["required_challenge_id"], actor_id=_actor(request).pk
            )
        except CTFPermissionError:
            return _forbidden(request)
        except CTFNotFoundError:
            return _not_found(request, "Challenge not found.")
        except (CTFStateError, CTFValidationError):
            return _bad_request(request, "Invalid prerequisite request.")
        return Response(
            {
                "id": str(prereq.id),
                "required_challenge_id": str(prereq.required_challenge_id),
                "required_challenge_name": prereq.required_challenge.name,
            },
            status=status.HTTP_201_CREATED,
        )


class PrerequisiteDeleteView(APIView):
    """Remove a challenge prerequisite."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=DeleteSuccessSerializer)
    def post(self, request: Request, prerequisite_id: UUID) -> Response:
        """Resolve the prerequisite, enforce ownership, and delete via the service."""
        from ctf.models import CTFChallengePrerequisite
        from ctf.services.challenge import remove_prerequisite

        try:
            prereq = CTFChallengePrerequisite.objects.select_related("challenge__event").get(pk=prerequisite_id)
        except CTFChallengePrerequisite.DoesNotExist:
            return _not_found(request, "Prerequisite not found")
        if prereq.challenge.event.created_by_id != _actor(request).pk:
            return _forbidden(request)
        return _delete_via_service(request, remove_prerequisite, prerequisite_id)


class RateChallengeView(APIView):
    """Record a participant's rating for a challenge (1-5)."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = _PLAY_WRITE

    @extend_schema(request=RateChallengeRequestSerializer, responses=RateChallengeResultSerializer)
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Validate the rating and record it for the resolved participant."""
        from ctf.exceptions import CTFNotFoundError, CTFValidationError
        from ctf.services.submission import rate_challenge

        participant, error = _resolve_challenge_participant(request, challenge_id)
        if error is not None:
            return error
        assert participant is not None
        serializer = RateChallengeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            rating = rate_challenge(participant.id, challenge_id, serializer.validated_data["value"])
        except CTFNotFoundError:
            return _not_found(request, _CHALLENGE_OR_PARTICIPANT_NOT_FOUND)
        except CTFValidationError:
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)
        return Response({"value": rating.value, "challenge_id": str(challenge_id)})


class SubmitFlagView(APIView):
    """Submit a flag for a challenge (participant)."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = _PLAY_WRITE

    @extend_schema(request=SubmitFlagRequestSerializer, responses=SubmitFlagResultSerializer)
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Validate the flag body and submit it for the resolved participant."""
        from shared.audit import get_client_ip

        participant, error = _resolve_challenge_participant(request, challenge_id)
        if error is not None:
            return error
        assert participant is not None
        serializer = SubmitFlagRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        flag = (serializer.validated_data.get("flag") or "").strip()
        if not flag:
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)
        return self._submit(request, participant, challenge_id, flag, get_client_ip(request))

    def _submit(
        self,
        request: Request,
        participant: CTFParticipant,
        challenge_id: UUID,
        flag: str,
        ip_address: str | None,
    ) -> Response:
        """Call the submission service and render the scored result or a mapped error."""
        from ctf.exceptions import CTFNotFoundError, CTFRateLimitError, CTFStateError, CTFValidationError
        from ctf.services.scoring import calculate_score, get_participant_rank
        from ctf.services.submission import submit_flag

        try:
            submission = submit_flag(participant.id, challenge_id, flag, ip_address=ip_address)
        except CTFNotFoundError:
            return _not_found(request, _CHALLENGE_OR_PARTICIPANT_NOT_FOUND)
        except (CTFValidationError, CTFStateError):
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)
        except CTFRateLimitError as exc:
            return self._rate_limited(request, exc)
        score = calculate_score(participant.id)
        rank = get_participant_rank(participant.id)
        return Response(
            {
                "correct": submission.is_correct,
                "points_awarded": submission.points_awarded,
                "attempt_number": submission.attempt_number,
                "score": score,
                "rank": rank,
                "message": "Correct!" if submission.is_correct else "Incorrect flag.",
            }
        )

    @staticmethod
    def _rate_limited(request: Request, exc: object) -> Response:
        """Render the 429 envelope, replicating the legacy ``Retry-After`` header."""
        retry_after = getattr(exc, "details", {}).get("retry_after_seconds")
        resp = api_error_response(
            code="throttled",
            message="Rate limit exceeded.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            request=request,
        )
        if retry_after:
            resp["Retry-After"] = str(int(retry_after))
        return resp


class UseHintView(APIView):
    """Unlock the next hint (or a specific hint) for a challenge (participant)."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = _PLAY_WRITE

    @extend_schema(request=UseHintRequestSerializer, responses=UseHintResultSerializer)
    def post(self, request: Request, challenge_id: UUID) -> Response:
        """Resolve which hint to unlock, then unlock it for the resolved participant."""
        participant, error = _resolve_challenge_participant(request, challenge_id)
        if error is not None:
            return error
        assert participant is not None
        hint_or_error = self._resolve_hint_to_unlock(request, participant, challenge_id)
        if isinstance(hint_or_error, Response):
            return hint_or_error
        return self._unlock(request, participant, challenge_id, hint_or_error)

    def _resolve_hint_to_unlock(
        self, request: Request, participant: CTFParticipant, challenge_id: UUID
    ) -> UUID | Response:
        """Return the hint UUID to unlock, or a 400 ``Response``.

        Mirrors ``ctf.views.api.play._resolve_hint_to_unlock``: an explicit
        ``hint_id`` (even null/malformed) is parsed to a UUID or 400; an empty
        body falls through to the next not-yet-unlocked hint.
        """
        body = request.data if isinstance(request.data, dict) else None
        if body is None:
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)
        if "hint_id" in body:
            return self._parse_explicit_hint_id(request, body)
        return self._resolve_next_unlockable_hint(request, participant, challenge_id)

    @staticmethod
    def _parse_explicit_hint_id(request: Request, body: dict[str, object]) -> UUID | Response:
        """Parse an explicit ``hint_id`` body field, returning the UUID or a 400."""
        from ctf.views._parsing import _BodyUUIDError, _parse_body_uuid

        try:
            return _parse_body_uuid(body.get("hint_id"), "hint_id")
        except _BodyUUIDError:
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)

    @staticmethod
    def _resolve_next_unlockable_hint(
        request: Request, participant: CTFParticipant, challenge_id: UUID
    ) -> UUID | Response:
        """Return the first not-yet-unlocked hint's UUID, or 400 when none remain."""
        from ctf.services.hint import get_hints, get_unlocked_hints

        unlocked_ids = {h.id for h in get_unlocked_hints(participant.id, challenge_id)}
        next_hint = next((h for h in get_hints(challenge_id) if h.id not in unlocked_ids), None)
        if not next_hint:
            return _bad_request(request, _NO_MORE_HINTS)
        return next_hint.id

    @staticmethod
    def _unlock(request: Request, participant: CTFParticipant, challenge_id: UUID, hint_id: UUID) -> Response:
        """Unlock the resolved hint, returning the result payload or a mapped error."""
        from ctf.exceptions import CTFNotFoundError, CTFStateError, CTFValidationError
        from ctf.services.hint import use_hint

        try:
            result = use_hint(participant.id, hint_id, expected_challenge_id=challenge_id)
        except CTFNotFoundError:
            return _not_found(request, _CHALLENGE_OR_PARTICIPANT_NOT_FOUND)
        except (CTFValidationError, CTFStateError):
            return _bad_request(request, _CHALLENGE_ACTION_FAILED)
        return Response(result)


class SubmissionListView(APIView):
    """List the requesting participant's own submissions."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=SubmissionListResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return the active participant's own submission history."""
        from ctf.services.submission import get_participant_submissions

        participant = _resolve_active_participant(request)
        if participant is None:
            return _not_found(request, _PARTICIPANT_NOT_FOUND)
        submissions = get_participant_submissions(participant.id)
        data = [
            {
                "id": str(s.id),
                "challenge_id": str(s.challenge_id),
                "challenge_name": s.challenge.name,
                "is_correct": s.is_correct,
                "points_awarded": s.points_awarded,
                "attempt_number": s.attempt_number,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            }
            for s in submissions.select_related("challenge")
        ]
        return Response({"submissions": data, "total": len(data)})


def _participant_detail_payload(participant: CTFParticipant) -> dict[str, object]:
    """Render the organizer GET-participant JSON payload.

    Mirrors ``ctf.views.api.participants._participant_detail_payload`` key-for-key.
    """
    from ctf.models import CTFSubmission

    submissions = CTFSubmission.objects.filter(participant=participant)
    correct_submissions = submissions.filter(is_correct=True)
    return {
        "id": str(participant.id),
        "name": participant.name,
        "email": participant.email,
        "status": participant.status,
        "team_name": participant.team.name if participant.team else None,
        "registered_at": participant.registered_at.isoformat() if participant.registered_at else None,
        "invited_at": participant.invited_at.isoformat() if participant.invited_at else None,
        "last_active_at": participant.last_active_at.isoformat() if participant.last_active_at else None,
        "total_score": participant.total_score,
        "solved_count": correct_submissions.count(),
        "attempt_count": submissions.count(),
        "event_id": str(participant.event_id),
    }


class ParticipantListView(APIView):
    """List an event's participants (GET) or invite one (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=ParticipantListResponseSerializer)
    def get(self, request: Request, event_id: UUID) -> Response:
        """Return the participants of an owned event, optionally filtered by status."""
        from ctf.services import list_participants_for_event

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        participants = list_participants_for_event(event_id)
        status_filter = request.query_params.get("status")
        if status_filter:
            participants = participants.filter(status=status_filter)
        data = [
            {
                "id": str(p.id),
                "name": p.name,
                "email": p.email,
                "status": p.status,
                "team_name": p.team.name if p.team else None,
                "registered_at": p.registered_at.isoformat() if p.registered_at else None,
                "total_score": p.total_score,
            }
            for p in participants
        ]
        return Response({"participants": data, "total": len(data)})

    @extend_schema(request=ParticipantInviteSerializer, responses={201: ParticipantInviteResultSerializer})
    def post(self, request: Request, event_id: UUID) -> Response:
        """Invite a single participant to an owned event."""
        from ctf.exceptions import CTFValidationError
        from ctf.services import invite_participant

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        serializer = ParticipantInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        email = serializer.validated_data["email"]
        try:
            participant = invite_participant(event_id, email, name)
        except CTFValidationError:
            return _bad_request(request, _INVALID_PARTICIPANT_REQUEST)
        return Response(
            {
                "id": str(participant.id),
                "name": participant.name,
                "email": participant.email,
                "status": participant.status,
                "invited": True,
            },
            status=status.HTTP_201_CREATED,
        )


class ParticipantImportView(APIView):
    """Bulk-import participants into an owned event (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=ParticipantImportSerializer, responses=ParticipantImportResultSerializer)
    def post(self, request: Request, event_id: UUID) -> Response:
        """Validate the import body and invite each row, collecting per-row errors."""
        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        serializer = ParticipantImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._import(event_id, serializer.validated_data["participants"])

    @staticmethod
    def _import(event_id: UUID, participants_data: list[object]) -> Response:
        """Invite each row, mirroring the legacy per-item validation and error shapes."""
        from ctf.exceptions import CTFValidationError
        from ctf.services import invite_participant

        imported: list[dict[str, str]] = []
        errors: list[dict[str, object]] = []
        for idx, p_data in enumerate(participants_data):
            # Each element must be an object; a bare scalar passed the list guard
            # but would raise AttributeError on .get() and surface as a 500
            # (#1149). Report it per-item instead.
            if not isinstance(p_data, dict):
                errors.append({"index": idx, "error": "each participant must be an object"})
                continue
            name = p_data.get("name")
            email = p_data.get("email")
            if not name or not email:
                errors.append({"index": idx, "error": "name and email are required"})
                continue
            try:
                participant = invite_participant(event_id, email, name)
                imported.append(
                    {
                        "id": str(participant.id),
                        "name": participant.name,
                        "email": participant.email,
                    }
                )
            except CTFValidationError as exc:
                logger.warning("CTF participant import row %s failed: %s", idx, safe_log_value(str(exc)))
                errors.append({"index": idx, "email": email, "error": "Could not import participant."})
        return Response({"imported": len(imported), "participants": imported, "errors": errors})


class ParticipantDetailView(APIView):
    """Get (GET) or soft-delete (DELETE) a single owned participant."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=ParticipantDetailSerializer)
    def get(self, request: Request, participant_id: UUID) -> Response:
        """Return the full organizer participant detail projection."""
        participant, error = _resolve_owned_participant(request, participant_id)
        if error is not None:
            return error
        assert participant is not None
        return Response(_participant_detail_payload(participant))

    @extend_schema(responses=ParticipantDeleteResultSerializer)
    def delete(self, request: Request, participant_id: UUID) -> Response:
        """Soft-delete an owned participant."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services import delete_participant

        _participant, error = _resolve_owned_participant(request, participant_id)
        if error is not None:
            return error
        try:
            delete_participant(participant_id)
        except CTFNotFoundError:
            return _not_found(request, _PARTICIPANT_NOT_FOUND)
        return Response({"deleted": True, "id": str(participant_id)})


class ParticipantResendInviteView(APIView):
    """Reset and resend a participant's credentials (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=ResendInviteResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Rate-limit, enforce ownership, then reset and resend the invite."""
        from ctf.exceptions import CTFStateError, CTFValidationError
        from ctf.services import resend_invite
        from ctf.views._access import _check_credential_delivery_rate_limit

        if not _check_credential_delivery_rate_limit(_actor(request).pk):
            return api_error_response(
                code="throttled",
                message="Too many invitations. Try again later.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                request=request,
            )
        _participant, error = _resolve_owned_participant(request, participant_id)
        if error is not None:
            return error
        try:
            updated = resend_invite(participant_id)
        except (CTFStateError, CTFValidationError):
            # CTFValidationError covers the fail-closed bootstrap-credential path
            # (issue #1665): an unavailable/invalid configured source must surface
            # as a controlled 400, never an uncaught 500.
            return _bad_request(request, _INVALID_PARTICIPANT_REQUEST)
        return Response({"success": True, "id": str(updated.id), "invited": True})


class AssignBracketView(APIView):
    """Assign or remove a participant's bracket (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=AssignBracketRequestSerializer, responses=AssignBracketResultSerializer)
    def post(self, request: Request, participant_id: UUID) -> Response:
        """Enforce ownership, then assign (bracket_id given) or remove (null) the bracket."""
        _participant, error = _resolve_owned_participant(request, participant_id)
        if error is not None:
            return error
        serializer = AssignBracketRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._set_bracket(request, participant_id, serializer.validated_data.get("bracket_id"))

    @staticmethod
    def _set_bracket(request: Request, participant_id: UUID, bracket_id: object) -> Response:
        """Assign (bracket_id given) or remove (bracket_id None) a participant's bracket."""
        if bracket_id is None:
            from ctf.services.bracket import remove_participant_bracket

            remove_participant_bracket(participant_id)
            return Response({"status": "ok", "bracket": None})

        from uuid import UUID as _UUID

        from django.core.exceptions import ValidationError

        from ctf.models import CTFBracket
        from ctf.services.bracket import assign_participant_bracket

        try:
            bracket_uuid = _UUID(str(bracket_id))
            participant = assign_participant_bracket(participant_id, bracket_uuid)
        except ValueError:
            return _bad_request(request, "Invalid bracket ID format")
        except ValidationError:
            return _bad_request(request, "Bracket and participant must belong to the same event")
        except CTFBracket.DoesNotExist:
            return _not_found(request, _BRACKET_NOT_FOUND)
        bracket = participant.bracket
        return Response(
            {
                "status": "ok",
                "bracket": {"id": str(bracket.id), "name": bracket.name} if bracket else None,
            }
        )


# ---------------------------------------------------------------------------
# Range lifecycle views (participant status/access + organizer range ops)
# ---------------------------------------------------------------------------


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

    _participant, error = _resolve_owned_participant(request, participant_id)
    if error is not None:
        return error
    try:
        result = action_fn(participant_id)
    except (CTFNotFoundError, CTFRangeError):
        return _bad_request(request, _RANGE_REQUEST_FAILED)
    return Response(result)


class ParticipantRangeStatusView(APIView):
    """Return the range status for the requesting participant's active event."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=RangeStatusResponseSerializer)
    def get(self, request: Request) -> Response:
        """Return the participant's range status, or the not-assigned sentinel."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services import range as range_service

        participant = _resolve_active_participant(request)
        if participant is None:
            return Response({"status": "not_assigned", "range_instance_id": None})
        try:
            result = range_service.get_range_status(participant.pk)
        except CTFNotFoundError:
            return _not_found(request, _PARTICIPANT_NOT_FOUND)
        return Response(result)


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

        event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        assert event is not None
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

        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
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
        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        return self._provision_spares(request, event_id)

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
            return _bad_request(request, _SPARE_POOL_REQUEST_FAILED)
        try:
            result = provision_event_spares(event_id, count, operator=_actor(request))
        except CTFNotFoundError:
            return _not_found(request, _EVENT_NOT_FOUND)
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
        _participant, error = _resolve_owned_participant(request, participant_id)
        if error is not None:
            return error
        return self._recover(request, participant_id)

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
            return _bad_request(request, _RECOVERY_REQUEST_FAILED)
        except CTFNotFoundError:
            return _not_found(request, _PARTICIPANT_NOT_FOUND)
        except (CTFValidationError, CTFRangeError):
            return _bad_request(request, _RECOVERY_REQUEST_FAILED)
        return Response(result)


class SendInvitationsView(APIView):
    """Send invitation emails to all uninvited participants of an owned event."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=SendInvitationsResultSerializer)
    def post(self, request: Request, event_id: UUID) -> Response:
        """Rate-limit, enforce ownership, then queue the invitation emails."""
        from ctf.services.notification import send_invitations
        from ctf.views._access import _check_credential_delivery_rate_limit

        if not _check_credential_delivery_rate_limit(_actor(request).pk):
            return api_error_response(
                code="throttled",
                message="Too many invitations. Try again later.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                request=request,
            )
        _event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        result = send_invitations(event_id)
        return Response({"success": True, **result})


# ---------------------------------------------------------------------------
# Notification views (organizer announcements + email-template overrides)
# ---------------------------------------------------------------------------


def _dispatch_notification_send(notif: CTFNotification) -> None:
    """Send a notification via the handler matching its type (logging an unknown type).

    Mirrors ``ctf.views.api.notifications._dispatch_notification_send``.
    """
    from ctf.enums import NotificationType
    from ctf.services import notification

    type_dispatch = {
        NotificationType.INVITE.value: lambda n: notification.send_invitations(n.event_id),
        NotificationType.CREDENTIALS.value: lambda n: notification.send_credentials(n.event_id),
        NotificationType.REMINDER.value: lambda n: notification.send_reminder(n.event_id),
        NotificationType.ANNOUNCEMENT.value: lambda n: notification.send_announcement(
            n.event_id, n.subject, n.body, n.created_by
        ),
    }
    handler = type_dispatch.get(notif.notification_type)
    if handler:
        handler(notif)
    else:
        logger.warning("No handler for notification type: %s", safe_log_value(str(notif.notification_type)))


def _email_template_payload(template: object) -> dict[str, object]:
    """Render the per-event email-template JSON payload.

    Mirrors ``ctf.views.api.notifications._handle_get_email_template`` /
    ``_handle_put_email_template`` key-for-key.
    """
    return {
        "id": str(template.id),  # type: ignore[attr-defined]
        "notification_type": template.notification_type,  # type: ignore[attr-defined]
        "subject": template.subject,  # type: ignore[attr-defined]
        "html_body": template.html_body,  # type: ignore[attr-defined]
        "text_body": template.text_body,  # type: ignore[attr-defined]
    }


def _validate_email_template_bodies(
    request: Request, html_body: str, text_body: str, notification_type: str
) -> Response | None:
    """Validate the two organizer email-template bodies; return a 400 or None.

    Mirrors ``ctf.views.api.notifications._validate_template_bodies``: request
    input is never compiled into a Django ``Template``; only the flat
    ``{{ name }}`` placeholder grammar over the per-type scalar allowlist is
    permitted (CWE-1336, issue #1095).
    """
    from ctf.services.email_template import allowed_placeholders, find_template_violations

    allowed = allowed_placeholders(notification_type)
    for label, source in (("html_body", html_body), ("text_body", text_body)):
        if not source:
            return _bad_request(request, "html_body and text_body are required")
        violations = find_template_violations(source, allowed)
        if violations:
            return _bad_request(request, f"Invalid template syntax in {label}: {violations[0]}")
    return None


class NotificationListView(APIView):
    """List an event's notifications (GET) or send an announcement (POST)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    @extend_schema(responses=NotificationListResponseSerializer)
    def get(self, request: Request, event_id: UUID) -> Response:
        """Return the notifications for an owned event, newest first."""
        from ctf.models import CTFNotification

        event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        assert event is not None
        data = [
            {
                "id": str(n.id),
                "notification_type": n.notification_type,
                "subject": n.subject,
                "status": n.status,
                "sent_count": n.sent_count,
                "created_at": n.created_at.isoformat(),
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            }
            for n in CTFNotification.objects.filter(event=event).order_by("-created_at")
        ]
        return Response({"notifications": data})

    @extend_schema(request=NotificationAnnounceRequestSerializer, responses={201: NotificationAnnounceResultSerializer})
    def post(self, request: Request, event_id: UUID) -> Response:
        """Send an announcement to an owned event from the request body."""
        from ctf.services import notification

        event, error = _resolve_owned_event(request, event_id)
        if error is not None:
            return error
        assert event is not None
        serializer = NotificationAnnounceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subject = serializer.validated_data["subject"].strip()
        body = serializer.validated_data["body"].strip()
        if not subject or not body:
            return _bad_request(request, _INVALID_NOTIFICATION)
        notif = notification.send_announcement(
            event_id=event.id,
            subject=subject,
            body=body,
            created_by=_actor(request),
        )
        return Response(
            {
                "id": str(notif.id),
                "subject": notif.subject,
                "status": notif.status,
                "sent_count": notif.sent_count,
            },
            status=status.HTTP_201_CREATED,
        )


class NotificationSendView(APIView):
    """Dispatch a notification to its recipients (organizer)."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = _EVENT_WRITE

    @extend_schema(request=None, responses=NotificationSendResultSerializer)
    def post(self, request: Request, notification_id: UUID) -> Response:
        """Resolve the notification, enforce ownership, then send it."""
        from ctf.models import CTFNotification

        notif = CTFNotification.objects.select_related("event").filter(pk=notification_id).first()
        if not notif:
            return _not_found(request, _NOTIFICATION_NOT_FOUND)
        if notif.event.created_by_id != _actor(request).pk:
            return _forbidden(request)
        _dispatch_notification_send(notif)
        return Response({"notification_id": str(notif.id), "status": "sent"})


class EventEmailTemplateView(APIView):
    """Get, create/update, or revert a per-event email-template override."""

    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = _EVENT_READ
    required_write_scopes = _EVENT_WRITE

    def _resolve(
        self, request: Request, event_id: UUID, notification_type: str
    ) -> tuple[CTFEvent | None, Response | None]:
        """Validate the notification type (400) then resolve and own the event (404/403)."""
        from ctf.enums import NotificationType

        valid_types = {nt.value for nt in NotificationType}
        if notification_type not in valid_types:
            return None, _bad_request(request, f"Invalid notification type: {notification_type}")
        return _resolve_owned_event(request, event_id)

    @extend_schema(responses=EmailTemplateResponseSerializer)
    def get(self, request: Request, event_id: UUID, notification_type: str) -> Response:
        """Return the per-event custom template, or 404 when using the default."""
        from ctf.models import CTFEmailTemplate

        event, error = self._resolve(request, event_id, notification_type)
        if error is not None:
            return error
        assert event is not None
        template = CTFEmailTemplate.objects.filter(event=event, notification_type=notification_type).first()
        if template is None:
            return _not_found(request, "No custom template")
        return Response(_email_template_payload(template))

    @extend_schema(request=EmailTemplateWriteSerializer, responses=EmailTemplateResponseSerializer)
    def put(self, request: Request, event_id: UUID, notification_type: str) -> Response:
        """Create or update the per-event custom template from the request body."""
        from ctf.models import CTFEmailTemplate

        event, error = self._resolve(request, event_id, notification_type)
        if error is not None:
            return error
        assert event is not None
        serializer = EmailTemplateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        html_body = serializer.validated_data["html_body"].strip()
        text_body = serializer.validated_data["text_body"].strip()
        subject = serializer.validated_data["subject"].strip()
        syntax_error = _validate_email_template_bodies(request, html_body, text_body, notification_type)
        if syntax_error is not None:
            return syntax_error
        template, _created = CTFEmailTemplate.objects.update_or_create(
            event=event,
            notification_type=notification_type,
            defaults={"subject": subject, "html_body": html_body, "text_body": text_body},
        )
        return Response(_email_template_payload(template))

    @extend_schema(responses=EmailTemplateRevertResultSerializer)
    def delete(self, request: Request, event_id: UUID, notification_type: str) -> Response:
        """Soft-delete the per-event template, reverting to the platform default."""
        from ctf.models import CTFEmailTemplate

        event, error = self._resolve(request, event_id, notification_type)
        if error is not None:
            return error
        assert event is not None
        template = CTFEmailTemplate.objects.filter(event=event, notification_type=notification_type).first()
        if template is None:
            return _not_found(request, "No custom template to delete")
        template.delete(soft=True)
        return Response({"status": "reverted_to_default"})


# ---------------------------------------------------------------------------
# Scoreboard views (per-participant score timeline)
# ---------------------------------------------------------------------------


class ScoreTimelineView(APIView):
    """Return a participant's chronological score progression (role-based access)."""

    permission_classes = CTF_ROLE_PERMISSIONS
    required_read_scopes = _EVENT_OR_PLAY_READ

    @extend_schema(responses=ScoreTimelineResponseSerializer)
    def get(self, request: Request, participant_id: UUID) -> Response:
        """Resolve the participant, authorize access, and return the timeline."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services import get_participant
        from ctf.services.scoring import get_score_timeline

        try:
            participant = get_participant(participant_id)
        except CTFNotFoundError:
            return _not_found(request, _PARTICIPANT_NOT_FOUND)
        error = self._authorize(request, participant)
        if error is not None:
            return error
        timeline = get_score_timeline(participant_id)
        return Response(
            {
                "participant_id": str(participant.id),
                "participant_name": participant.name,
                "timeline": timeline,
            }
        )

    @staticmethod
    def _authorize(request: Request, participant: CTFParticipant) -> Response | None:
        """Authorize timeline access; organizers need event ownership, participants their own row.

        Mirrors ``ctf.views.api.scoreboard._authorize_timeline_access``.
        """
        from ctf.bridges import get_user_role

        actor = _actor(request)
        role = get_user_role(actor)
        if role.is_ctf_organizer:
            if participant.event.created_by_id != actor.pk:
                return _forbidden(request)
            return None
        if participant.user_id != actor.pk:
            return _forbidden(request)
        return None
