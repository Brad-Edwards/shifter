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
    ChallengeFileListResponseSerializer,
    ChallengeFileUploadResultSerializer,
    ChallengeFileUploadSerializer,
    ChallengeHintSerializer,
    ChallengeListResponseSerializer,
    ChallengeMutationResultSerializer,
    ChallengeWriteSerializer,
    DeleteSuccessSerializer,
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
    OrganizerChallengeDetailSerializer,
    PrerequisiteCreateResultSerializer,
    PrerequisiteListResponseSerializer,
    PrerequisiteWriteSerializer,
    RateChallengeRequestSerializer,
    RateChallengeResultSerializer,
    ScenarioListResponseSerializer,
)
from shared.api.errors import api_error_response
from shared.api_tokens import scopes

if TYPE_CHECKING:
    from uuid import UUID

    from django.contrib.auth.models import User

    from ctf.models import CTFChallenge, CTFChallengeFile, CTFEvent, CTFParticipant

_EVENT_READ = (scopes.CTF_EVENT_READ,)
_EVENT_WRITE = (scopes.CTF_EVENT_WRITE,)
_PLAY_WRITE = (scopes.CTF_PLAY_WRITE,)
_EVENT_OR_PLAY_READ = (scopes.CTF_EVENT_READ, scopes.CTF_PLAY_READ)
_INVALID_EVENT = "Invalid event request."
_EVENT_NOT_FOUND = "Event not found"
_FORBIDDEN = "Forbidden"
_CHALLENGE_NOT_FOUND = "Challenge not found"
_INVALID_CHALLENGE = "Invalid challenge request."


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

    @extend_schema(responses=DeleteSuccessSerializer)
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

    @extend_schema(responses={204: None})
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

    @extend_schema(responses=DeleteSuccessSerializer)
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

    @extend_schema(responses=DeleteSuccessSerializer)
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
            return _not_found(request, "Challenge or participant not found.")
        except CTFValidationError:
            return _bad_request(request, "Could not process challenge action.")
        return Response({"value": rating.value, "challenge_id": str(challenge_id)})
