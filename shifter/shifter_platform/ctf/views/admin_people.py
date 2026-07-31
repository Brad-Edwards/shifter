"""Organizer/admin participant, team, scoreboard, and range HTML views."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest


from ctf.views import _parsing
from ctf.views._access import (
    ctf_organizer_required,
)

logger = logging.getLogger(__name__)

_EVENT_NOT_FOUND_MSG = "Event not found"
_FORBIDDEN_EVENT_MSG = "Forbidden: You do not have access to this event"
_PARTICIPANT_NOT_FOUND_MSG = "Participant not found"
_PARTICIPANT_LIST_ROUTE = "ctf:admin_participant_list"
_PARTICIPANT_DETAIL_ROUTE = "ctf:admin_participant_detail"


@login_required
@ctf_organizer_required
def admin_participant_list(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Participant list for an event.

    Shows all participants with filtering by status and statistics.

    Args:
        event_id: UUID of the event.
    """
    from django.http import Http404

    from ctf.enums import ParticipantStatus
    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_event, list_participants_for_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        raise Http404(_EVENT_NOT_FOUND_MSG) from None

    # Check permission - organizers can only access their own events
    if event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)

    # Get participants with optional status filter
    participants = list_participants_for_event(event_id)
    status_filter = request.GET.get("status")

    if status_filter:
        participants = participants.filter(status=status_filter)

    # Calculate statistics
    all_participants = list_participants_for_event(event_id)
    total_count = all_participants.count()
    invited_count = all_participants.filter(status=ParticipantStatus.INVITED.value).count()
    registered_count = all_participants.filter(
        status__in=[
            ParticipantStatus.REGISTERED.value,
            ParticipantStatus.ACTIVE.value,
            ParticipantStatus.COMPLETED.value,
        ]
    ).count()

    # Get status choices for filter dropdown
    status_choices = ParticipantStatus.choices()

    context = {
        "event": event,
        "participants": participants,
        "status_filter": status_filter,
        "status_choices": status_choices,
        "total_count": total_count,
        "invited_count": invited_count,
        "registered_count": registered_count,
    }

    return render(request, "ctf/admin/participant_list.html", context)


@login_required
@ctf_organizer_required
@require_http_methods(["POST"])
def admin_participant_rename(request: HttpRequest, participant_id: UUID) -> HttpResponse:
    """Rename a participant's sole authentication handle."""
    from django.contrib import messages
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError, CTFValidationError
    from ctf.forms import CTFParticipantRenameForm
    from ctf.services.participant.accounts import rename_participant_username

    form = CTFParticipantRenameForm(request.POST)
    if form.is_valid():
        try:
            participant = rename_participant_username(
                participant_id,
                form.cleaned_data["username"],
                actor=request.user,
            )
        except CTFNotFoundError:
            raise Http404(_PARTICIPANT_NOT_FOUND_MSG) from None
        except CTFValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Participant username updated.")
            return redirect(_PARTICIPANT_DETAIL_ROUTE, participant_id=participant.pk)
    messages.error(request, "Invalid participant username.")
    return redirect(_PARTICIPANT_DETAIL_ROUTE, participant_id=participant_id)


@login_required
@ctf_organizer_required
@require_http_methods(["POST"])
def admin_participant_email(request: HttpRequest, participant_id: UUID) -> HttpResponse:
    """Attach or clear a participant's delivery-only email address."""
    from django.contrib import messages
    from django.http import Http404

    from ctf.forms import CTFParticipantEmailForm
    from ctf.models import CTFParticipant

    try:
        participant = CTFParticipant.objects.select_related("event").get(
            pk=participant_id,
            deleted_at__isnull=True,
        )
    except CTFParticipant.DoesNotExist:
        raise Http404(_PARTICIPANT_NOT_FOUND_MSG) from None
    if participant.event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)
    form = CTFParticipantEmailForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid delivery email.")
    else:
        participant.email = form.cleaned_data["email"].strip().lower()
        participant.save(update_fields=["email", "updated_at"])
        messages.success(request, "Delivery email updated.")
    return redirect(_PARTICIPANT_DETAIL_ROUTE, participant_id=participant_id)


@login_required
@ctf_organizer_required
@never_cache
def admin_participant_detail(request: HttpRequest, participant_id: UUID) -> HttpResponse:
    """Participant detail view.

    Shows participant profile, submission history, and actions.

    Args:
        participant_id: UUID of the participant.
    """
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError
    from ctf.models import CTFSubmission
    from ctf.services import get_participant

    try:
        participant = get_participant(participant_id)
    except CTFNotFoundError:
        raise Http404(_PARTICIPANT_NOT_FOUND_MSG) from None

    # Check permission - organizers can only access their own events' participants
    if participant.event.created_by_id != request.user.pk:
        return HttpResponse("Forbidden: You do not have access to this participant", status=403)

    # Get submission history
    submissions = (
        CTFSubmission.objects.filter(participant=participant).select_related("challenge").order_by("-submitted_at")
    )

    # Calculate statistics
    total_score = participant.total_score
    solved_count = submissions.filter(is_correct=True).count()
    total_attempts = submissions.count()
    context = {
        "participant": participant,
        "event": participant.event,
        "submissions": submissions,
        "total_score": total_score,
        "solved_count": solved_count,
        "total_attempts": total_attempts,
        "generated_issuance_kind": "generated",
        "supplied_issuance_kind": "set",
    }

    return render(request, "ctf/admin/participant_detail.html", context)


@login_required
@ctf_organizer_required
@never_cache
@sensitive_post_parameters("password", "password_confirm")
@require_http_methods(["POST"])
def admin_participant_password(request: HttpRequest, participant_id: UUID) -> HttpResponse:
    """Issue one participant password and render it only in this response."""
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError, CTFValidationError
    from ctf.services import get_participant, reset_participant_password
    from ctf.services.event import actor_has_event_capability
    from ctf.views._access import _check_credential_delivery_rate_limit
    from shared.audit import RequestAudit, get_client_ip, get_request_id

    try:
        participant = get_participant(participant_id)
    except CTFNotFoundError:
        raise Http404(_PARTICIPANT_NOT_FOUND_MSG) from None
    if not actor_has_event_capability(request.user, participant.event, "participants"):
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)
    actor = cast("User", request.user)
    actor_id = actor.pk
    if actor_id is None:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)
    try:
        allowed = _check_credential_delivery_rate_limit(actor_id)
    except Exception:
        return HttpResponse("Credential service is temporarily unavailable.", status=503)
    if not allowed:
        response = HttpResponse("Too many credential operations. Try again later.", status=429)
        response["Retry-After"] = "3600"
        return response

    kind = request.POST.get("kind", "")
    password = request.POST.get("password") if kind == "set" else None
    if kind == "set" and password != request.POST.get("password_confirm"):
        return HttpResponse("Passwords do not match.", status=400)
    try:
        issuance = reset_participant_password(
            participant_id,
            actor=actor,
            kind=kind,
            password=password,
            request_audit=RequestAudit(
                source_ip=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
                request_id=get_request_id(request),
            ),
        )
    except CTFValidationError:
        return HttpResponse("Invalid participant password request.", status=400)

    response = render(
        request,
        "ctf/admin/participant_password_result.html",
        {
            "participant": participant,
            "event": participant.event,
            "issuance": issuance,
        },
    )
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    response["Vary"] = "Cookie"
    return response


@login_required
@ctf_organizer_required
@require_http_methods(["GET", "POST"])
def admin_participant_add(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Add a single participant to an event.

    GET: Show add participant form.
    POST: Create participant and optionally send invite.

    Args:
        event_id: UUID of the event.
    """
    from django.contrib import messages
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError, CTFValidationError
    from ctf.forms import CTFParticipantForm
    from ctf.services import get_event, invite_participant

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        raise Http404(_EVENT_NOT_FOUND_MSG) from None

    # Check permission
    if event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)

    if request.method == "POST":
        form = CTFParticipantForm(request.POST, event=event)
        if form.is_valid():
            try:
                participant = invite_participant(
                    event_id=event_id,
                    email=form.cleaned_data["email"],
                    name=form.cleaned_data["name"],
                )
                logger.info(
                    "User %s added participant %s to event %s",
                    request.user.email,
                    participant.email,
                    safe_log_value(event_id),
                )
                messages.success(request, f"Participant {participant.name} added successfully.")
                return redirect(_PARTICIPANT_LIST_ROUTE, event_id=event_id)
            except CTFValidationError as e:
                form.add_error(None, str(e))
    else:
        form = CTFParticipantForm(event=event)

    context = {
        "event": event,
        "form": form,
        "is_add": True,
    }

    return render(request, "ctf/admin/participant_form.html", context)


@login_required
@ctf_organizer_required
def admin_team_list(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Team list for an event.

    Args:
        event_id: UUID of the event.
    """
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        raise Http404(_EVENT_NOT_FOUND_MSG) from None

    if event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)

    from ctf.models import CTFTeam

    teams = CTFTeam.objects.filter(event=event).select_related("captain").order_by("name")

    return render(
        request,
        "ctf/admin/team_list.html",
        {"event": event, "teams": teams},
    )


@login_required
@ctf_organizer_required
def admin_scoreboard(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Admin scoreboard view with extra details.

    Supports bracket filtering via ?bracket=<uuid> query parameter.

    Args:
        event_id: UUID of the event.
    """
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        raise Http404(_EVENT_NOT_FOUND_MSG) from None

    if event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)

    from ctf.services import get_event_stats, get_scoreboard, get_team_scoreboard

    stats = get_event_stats(event)
    brackets, selected_bracket, bracket_id = _parsing._resolve_bracket_filter(event.id, request.GET.get("bracket"))

    rankings = get_team_scoreboard(event.id) if event.team_mode else get_scoreboard(event.id)

    bracket_rankings = None
    if bracket_id:
        bracket_rankings = (
            get_team_scoreboard(event.id, bracket_id=bracket_id)
            if event.team_mode
            else get_scoreboard(event.id, bracket_id=bracket_id)
        )

    return render(
        request,
        "ctf/admin/scoreboard.html",
        {
            "event": event,
            "rankings": rankings,
            "bracket_rankings": bracket_rankings,
            "brackets": brackets,
            "selected_bracket": selected_bracket,
            "team_mode": event.team_mode,
            "stats": stats,
            "frozen": event.is_scoreboard_frozen,
        },
    )


@login_required
@ctf_organizer_required
def admin_range_list(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Range status overview for an event.

    Args:
        event_id: UUID of the event.
    """
    from django.http import Http404

    from ctf.exceptions import CTFNotFoundError
    from ctf.models import CTFParticipant
    from ctf.services import get_event

    try:
        event = get_event(event_id)
    except CTFNotFoundError:
        raise Http404(_EVENT_NOT_FOUND_MSG) from None

    if event.created_by_id != request.user.pk:
        return HttpResponse(_FORBIDDEN_EVENT_MSG, status=403)

    participants = list(CTFParticipant.objects.filter(event=event).order_by("name"))

    from ctf.enums import RecoveryStrategy
    from ctf.services import range as range_service

    progress = range_service.get_provision_progress(event_id)
    active_provisioning = bool(progress["task"]) or progress["counts"]["provisioning"] > 0

    # Bounded operator diagnostics only (phase + authored failure_category);
    # attached per-participant so the template can display it without a
    # dictionary-lookup-by-variable-key filter (issue #1018).
    for p in participants:
        p.recovery_status = range_service.get_recovery_status(p.pk)  # type: ignore[attr-defined]

    spare_summary = range_service.get_event_spare_summary(event_id)

    return render(
        request,
        "ctf/admin/range_list.html",
        {
            "event": event,
            "participants": participants,
            "active_provisioning": active_provisioning,
            "recovery_strategy_choices": RecoveryStrategy.choices(),
            "spare_summary": spare_summary,
        },
    )
