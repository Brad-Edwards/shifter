"""Participant-facing HTML views (registration, dashboard, range, scoreboard, team)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

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
@ensure_csrf_cookie
def ctf_register(request: HttpRequest) -> HttpResponse:
    """Render the magic-link exchange page (no token read, no login).

    The invite token is carried in the URL *fragment* (``#token=...``), which
    browsers never send to the server, so it stays out of the request target,
    proxy/ALB access logs, the ECS request formatter, and the ``Referer`` header
    (SonarCloud ``pythonenterprise:S8435``). This view only renders the exchange
    page and sets the CSRF cookie; page JavaScript reads the fragment, scrubs it
    from history, and POSTs the token to ``ctf_register_exchange`` for validation
    and login. A ``Referrer-Policy: no-referrer`` header is set as defense in
    depth so the address-bar fragment cannot leak via outbound navigations.
    """
    complete_pending = bool(request.user.is_authenticated and request.session.get("ctf_pending_invite_id"))
    response = render(
        request,
        "ctf/participant/register.html",
        {"complete_pending": complete_pending},
    )
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_POST
@ensure_csrf_cookie
def ctf_register_exchange(request: HttpRequest) -> JsonResponse:
    """Consume an invite token from the JSON body and create a session.

    New participants are onboarded at exchange time. Existing platform accounts
    must authenticate through the normal login flow before the invite enrolls
    them. Token consumption is atomic and one-time.
    """
    from ctf.services.participant import exchange_invite_token
    from ctf.views import _parsing

    try:
        body = _parsing._parse_body_object(request)
        token = _parsing._get_body_str(body, "token", required=True).strip()
    except _parsing._BodyParseError as e:
        return JsonResponse({"error": str(e)}, status=400)

    result = exchange_invite_token(request, token)
    payload: dict[str, str | bool] = {}
    if result.error:
        payload["error"] = result.error
    if result.redirect:
        payload["redirect"] = result.redirect
    if result.requires_login:
        payload["requires_login"] = True
        if result.login_url:
            payload["login_url"] = result.login_url
    return JsonResponse(payload, status=result.http_status)


@require_POST
@ensure_csrf_cookie
def ctf_register_complete(request: HttpRequest) -> JsonResponse:
    """Complete a pending invite after the holder signed in with an existing account."""
    from ctf.services.participant import complete_pending_invite

    result = complete_pending_invite(request)
    payload: dict[str, str] = {}
    if result.error:
        payload["error"] = result.error
    if result.redirect:
        payload["redirect"] = result.redirect
    return JsonResponse(payload, status=result.http_status)


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
            elif participant.team_id == team.id:
                error = "You are already on this team."
            else:
                # Serialize concurrent joins on the team so the capacity check and
                # the membership write cannot race past team_size_limit (#1140):
                # lock the team row, then re-check is_full under the lock.
                with transaction.atomic():
                    locked_team = CTFTeam.objects.select_for_update().get(pk=team.pk)
                    if locked_team.is_full:
                        error = "This team is full."
                    else:
                        _join_team_and_recompute(participant, locked_team)
                if error is None:
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
