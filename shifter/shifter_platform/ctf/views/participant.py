"""Participant-facing HTML views (registration, dashboard, range, scoreboard, team)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

if TYPE_CHECKING:
    from django.http import HttpRequest

    from ctf.models import (
        CTFParticipant,
        CTFTeam,
    )

from ctf.views import _access, _parsing
from ctf.views._access import (
    ctf_participant_required,
)

logger = logging.getLogger(__name__)

_SCOREBOARD_TEMPLATE = "ctf/participant/scoreboard.html"


@require_GET
def ctf_register(request: HttpRequest) -> HttpResponse:
    """Authenticate a participant via magic link token.

    No login required. The invite token IS the authentication.
    Participants are auto-registered at add-time via _auto_register_participant(),
    so every valid token maps to a participant with a linked Django user.

    Token expiration is enforced via is_invite_valid. When MAGIC_LINK_SINGLE_USE
    is True, the token is cleared after the first successful login.
    """
    from django.conf import settings
    from django.contrib.auth import login

    from ctf.models import CTFParticipant

    # S8435 (sensitive data in URL): the invite token is a single-use magic-link
    # credential delivered by email; URL delivery is the intended registration
    # mechanism, preserved verbatim by the ctf/views split (#885). Reworking the
    # flow off the query string is tracked in #1088.
    token = request.GET.get("token")  # NOSONAR
    participant = CTFParticipant.objects.filter(invite_token=token).select_related("user").first() if token else None

    error_message = None
    if not token:
        error_message = "Missing invite token."
    elif not participant or not participant.user:
        error_message = "Invalid invite token."
    elif not participant.is_invite_valid:
        error_message = "Invite token has expired."
    if error_message:
        return HttpResponse(error_message, status=400)

    # error_message is None implies a valid participant with a linked user.
    assert participant is not None and participant.user is not None
    login(request, participant.user, backend="django.contrib.auth.backends.ModelBackend")

    if getattr(settings, "MAGIC_LINK_SINGLE_USE", False):
        participant.invite_token = ""  # nosec B105 — clearing token, not a password  # NOSONAR
        participant.save(update_fields=["invite_token", "updated_at"])

    return redirect("mission_control:dashboard")


@login_required
@ctf_participant_required
def participant_dashboard(request: HttpRequest) -> HttpResponse:
    """Participant main dashboard.

    Shows event overview, challenge progress, and quick links.
    """
    from ctf.services.challenge import get_available_challenges
    from ctf.services.scoring import calculate_score, get_participant_rank

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, "ctf/participant/dashboard.html", {})

    event = participant.event
    score = calculate_score(participant.id)
    rank = get_participant_rank(participant.id)
    solved_count = participant.solved_challenge_count
    total_challenges = get_available_challenges(event.id).count()

    context = {
        "participant": participant,
        "event": event,
        "score": score,
        "rank": rank,
        "solved_count": solved_count,
        "total_challenges": total_challenges,
    }
    return render(request, "ctf/participant/dashboard.html", context)


@login_required
@ctf_participant_required
def participant_event(request: HttpRequest) -> HttpResponse:
    """Participant event detail view.

    Shows current event information and status.
    """

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, "ctf/participant/event.html", {})

    event = participant.event

    context = {
        "participant": participant,
        "event": event,
    }
    return render(request, "ctf/participant/event.html", context)


@login_required
@ctf_participant_required
def participant_range(request: HttpRequest) -> HttpResponse:
    """Participant range status and access.

    Shows range provisioning status and access URLs.
    """

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, "ctf/participant/range.html", {})

    # Look up provisioned instances (with IPs) via CMS services
    target_instances = []
    if participant.range_instance_id and participant.range_status == "ready" and participant.user:
        import cms.services as cms_services

        target_instances = cms_services.get_range_target_instances(participant.user.pk)

    context = {
        "participant": participant,
        "event": participant.event,
        "range_instance_id": participant.range_instance_id,
        "range_status": participant.range_status,
        "target_instances": target_instances,
    }
    return render(request, "ctf/participant/range.html", context)


@login_required
@ctf_participant_required
def scoreboard(request: HttpRequest) -> HttpResponse:
    """Public scoreboard view.

    Shows rankings for current event. Supports bracket filtering
    via ?bracket=<uuid> query parameter.
    """
    from ctf.services.scoring import get_scoreboard, get_team_scoreboard

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, _SCOREBOARD_TEMPLATE, {})

    event = participant.event

    # If organizer has hidden the scoreboard, show a hidden message
    if not event.scoreboard_visible:
        return render(
            request,
            _SCOREBOARD_TEMPLATE,
            {"participant": participant, "event": event, "scoreboard_hidden": True},
        )

    freeze_at = event.scoreboard_freeze_at if event.is_scoreboard_frozen else None
    brackets, selected_bracket, bracket_id = _parsing._resolve_bracket_filter(event.id, request.GET.get("bracket"))

    rankings = (
        get_team_scoreboard(event.id, freeze_at=freeze_at)
        if event.team_mode
        else get_scoreboard(event.id, freeze_at=freeze_at)
    )

    bracket_rankings = None
    if bracket_id:
        bracket_rankings = (
            get_team_scoreboard(event.id, freeze_at=freeze_at, bracket_id=bracket_id)
            if event.team_mode
            else get_scoreboard(event.id, freeze_at=freeze_at, bracket_id=bracket_id)
        )

    context = {
        "participant": participant,
        "event": event,
        "rankings": rankings,
        "bracket_rankings": bracket_rankings,
        "brackets": brackets,
        "selected_bracket": selected_bracket,
        "team_mode": event.team_mode,
        "frozen": event.is_scoreboard_frozen,
    }
    return render(request, _SCOREBOARD_TEMPLATE, context)


@login_required
@ctf_participant_required
def participant_team(request: HttpRequest) -> HttpResponse:
    """Participant team view.

    Shows team members and team-specific information.
    """

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, "ctf/participant/team.html", {})

    team = participant.team
    members = []
    team_score = 0

    if team:
        members = list(team.members.select_related("user"))
        team_score = team.total_score

    context = {
        "participant": participant,
        "event": participant.event,
        "team": team,
        "members": members,
        "team_score": team_score,
    }
    return render(request, "ctf/participant/team.html", context)


def _join_team_and_recompute(participant: CTFParticipant, team: CTFTeam) -> None:
    """Move a participant onto a team and refresh both teams' materialized scores.

    Issue #850: membership changed, so the joined team and the team the
    participant left (if any) both need their materialized leaderboard columns
    recomputed.
    """
    from ctf.services.scoring import recompute_team_score

    old_team_id = participant.team_id
    participant.team = team
    participant.save(update_fields=["team", "updated_at"])
    recompute_team_score(team.id)
    if old_team_id is not None and old_team_id != team.id:
        recompute_team_score(old_team_id)


@login_required
@ctf_participant_required
@require_http_methods(["GET", "POST"])
def team_join(request: HttpRequest) -> HttpResponse:
    """Join a team using invite code.

    GET: Show join form.
    POST: Process join request.
    """
    from django.shortcuts import redirect

    from ctf.models import CTFTeam

    participant = _access._get_active_participant(request)
    if not participant:
        return render(request, "ctf/participant/team_join.html", {})

    event = participant.event
    error = None

    if request.method == "POST":
        invite_code = request.POST.get("invite_code", "").strip()
        if not invite_code:
            error = "Invite code is required."
        else:
            team = CTFTeam.objects.filter(event=event, invite_code=invite_code).first()
            if not team:
                error = "Invalid invite code."
            elif team.is_full:
                error = "This team is full."
            elif participant.team_id == team.id:
                error = "You are already on this team."
            else:
                _join_team_and_recompute(participant, team)
                logger.info(
                    "Participant %s joined team %s in event %s",
                    participant.id,
                    team.id,
                    event.id,
                )
                return redirect("ctf:participant_team")

    context = {
        "participant": participant,
        "event": event,
        "error": error,
    }
    return render(request, "ctf/participant/team_join.html", context)


@require_GET
def ctf_help(request: HttpRequest) -> HttpResponse:
    """CTF help page.

    Public help page for CTF participants.
    """
    return render(request, "ctf/help.html")
