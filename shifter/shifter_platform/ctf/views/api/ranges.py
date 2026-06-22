"""Range lifecycle JSON API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


from ctf.views import _access
from ctf.views._access import (
    _check_invite_rate_limit,
    _get_user,
    ctf_organizer_required,
    ctf_participant_required,
)
from ctf.views.api._common import (
    _resolve_owned_event_json,
)

logger = logging.getLogger(__name__)


@login_required
@ctf_participant_required
@require_GET
def api_range_status(request: HttpRequest) -> JsonResponse:
    """API: Get range status for current participant."""
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import range as range_service

    participant = _access._get_active_participant(request)

    if not participant:
        return JsonResponse({"status": "not_assigned", "range_instance_id": None})

    try:
        status = range_service.get_range_status(participant.pk)
        return JsonResponse(status)
    except CTFNotFoundError:
        return JsonResponse({"error": "Participant not found"}, status=404)


@login_required
@ctf_participant_required
@require_POST
def api_range_access(request: HttpRequest) -> JsonResponse:
    """API: Get range access URL.

    Delegates to mission_control's Guacamole RDP endpoint.
    CTF participants are standard users with ranges — the platform's
    existing RDP access flow works for them directly.
    """
    from django.urls import reverse

    return JsonResponse(
        {
            "redirect": reverse("mission_control:guacamole_rdp_url"),
            "message": "Use the mission_control RDP endpoint directly.",
        }
    )


@login_required
@ctf_organizer_required
@require_GET
def api_range_list(request: HttpRequest, event_id: UUID) -> JsonResponse:
    """API: Range status for all participants in an event.

    Args:
        event_id: UUID of the event.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.models import CTFParticipant
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        return JsonResponse({"error": "Event not found"}, status=404)

    if event.created_by_id != request.user.pk:
        return JsonResponse({"error": "Forbidden"}, status=403)

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

    from ctf.services import range as range_service

    progress = range_service.get_provision_progress(event_id)

    return JsonResponse(
        {
            "event_id": str(event_id),
            "ranges": data,
            "progress": progress,
        }
    )


@login_required
@ctf_organizer_required
@require_POST
def api_provision_ranges(request: HttpRequest, event_id: UUID) -> JsonResponse:
    """API: Queue bulk range provisioning for an event.

    Enqueues (or coalesces onto) a background spin-up task and returns
    immediately so the request thread is never blocked by the throttled
    provisioning loop. Progress is polled via ``api_range_list``.

    Args:
        event_id: UUID of the event.
    """
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        return JsonResponse({"error": "Event not found"}, status=404)

    if event.created_by_id != request.user.pk:
        return JsonResponse({"error": "Forbidden"}, status=403)

    from ctf.services import range as range_service

    task = range_service.request_event_provisioning(event_id, source="manual")
    return JsonResponse(
        {
            "event_id": str(event_id),
            "status": "queued",
            "task_id": str(task.pk),
            "task_status": task.status,
        },
        status=202,
    )


@login_required
@ctf_organizer_required
@require_POST
def api_provision_participant_range(request: HttpRequest, participant_id: UUID) -> JsonResponse:
    """API: Provision a range for a single participant."""
    from ctf.services import range as range_service

    return _participant_range_action(request, participant_id, range_service.provision_participant_range)


@login_required
@ctf_organizer_required
@require_POST
def api_destroy_participant_range(request: HttpRequest, participant_id: UUID) -> JsonResponse:
    """API: Destroy a range for a single participant."""
    from ctf.services import range as range_service

    return _participant_range_action(request, participant_id, range_service.destroy_participant_range)


def _run_participant_range_action(participant_id: UUID, action_fn: Callable[[UUID], Any]) -> JsonResponse:
    """Run a range action for a participant, returning its result or a 400 on a known range error."""
    from ctf.exceptions import CTFNotFoundError, CTFRangeError

    try:
        result = action_fn(participant_id)
    except (CTFNotFoundError, CTFRangeError) as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse(result)


def _participant_range_action(
    request: HttpRequest, participant_id: UUID, action_fn: Callable[[UUID], Any]
) -> JsonResponse:
    """Common logic for organizer range actions (stop, start, restart, etc.)."""
    from ctf.models import CTFParticipant

    try:
        participant = CTFParticipant.objects.select_related("event").get(pk=participant_id)
    except CTFParticipant.DoesNotExist:
        return JsonResponse({"error": "Participant not found"}, status=404)

    if participant.event.created_by_id != request.user.pk:
        return JsonResponse({"error": "Forbidden"}, status=403)

    return _run_participant_range_action(participant_id, action_fn)


@login_required
@ctf_organizer_required
@require_POST
def api_stop_participant_range(request: HttpRequest, participant_id: UUID) -> JsonResponse:
    """API: Stop (pause) a participant's range."""
    from ctf.services import range as range_service

    return _participant_range_action(request, participant_id, range_service.stop_participant_range)


@login_required
@ctf_organizer_required
@require_POST
def api_start_participant_range(request: HttpRequest, participant_id: UUID) -> JsonResponse:
    """API: Start (resume) a participant's stopped range."""
    from ctf.services import range as range_service

    return _participant_range_action(request, participant_id, range_service.start_participant_range)


@login_required
@ctf_organizer_required
@require_POST
def api_restart_participant_range(request: HttpRequest, participant_id: UUID) -> JsonResponse:
    """API: Restart a participant's range."""
    from ctf.services import range as range_service

    return _participant_range_action(request, participant_id, range_service.restart_participant_range)


@login_required
@ctf_organizer_required
@require_POST
def api_send_invitations(request: HttpRequest, event_id: UUID) -> JsonResponse:
    """API: Send invitation emails to all uninvited participants.

    Args:
        event_id: UUID of the event.
    """
    from ctf.services.notification import send_invitations

    if not _check_invite_rate_limit(_get_user(request).pk):
        return JsonResponse({"error": "Too many invitations. Try again later."}, status=429)

    _event, error = _resolve_owned_event_json(request, event_id)
    if error is not None:
        return error

    result = send_invitations(event_id)
    return JsonResponse({"success": True, **result})
