"""Canonical DRF participant read views for the CTF workspace SPA.

These are the typed participant-self reads (``/api/v1/ctf/me/*``) that the legacy
Django template views never exposed as JSON. Each view resolves the participant
with the same event-scoped policy the legacy participant pages use
(``get_user_role(actor).active_ctf_event`` then ``get_participant_by_user``,
codex #765/#768/#769) so a multi-event user always acts as the correct
participant, then returns a participant-safe projection from
:mod:`ctf.api.projections`. Authorization and token scopes are enforced by the
shared ``ctf.api._base`` participant permission set; these views add no new
authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ctf.api import projections
from ctf.api._base import CTF_PARTICIPANT_PERMISSIONS, ctf_actor_user
from ctf.api.serializers import (
    ParticipantChallengeListItemSerializer,
    ParticipantCurrentEventSerializer,
    ParticipantTeamSerializer,
)
from shared.api.errors import api_error_response
from shared.api_tokens import scopes

if TYPE_CHECKING:
    from ctf.models import CTFParticipant

_NO_ACTIVE_EVENT = "No active CTF event for this participant."
_PLAY_READ = (scopes.CTF_PLAY_READ,)


def _resolve_active_participant(request: Request) -> CTFParticipant | None:
    """Resolve the participant for the actor's active event, or ``None``.

    Mirrors ``ctf.views._access._get_active_participant``: the participant is
    scoped to ``get_user_role(actor).active_ctf_event`` rather than an unscoped
    first-row pick, so a user enrolled in several events acts as the right one.
    """
    actor = ctf_actor_user(request)
    if actor is None:
        return None
    from ctf.bridges import get_user_role
    from ctf.services.participant import get_participant_by_user

    role = get_user_role(actor)
    if role.active_ctf_event is None:
        return None
    return get_participant_by_user(actor, event_id=role.active_ctf_event.id)


class ParticipantCurrentEventView(APIView):
    """Return the participant's current event and their own participant state."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=ParticipantCurrentEventSerializer)
    def get(self, request: Request) -> Response:
        """Return the current-event projection, or 404 when there is none."""
        participant = _resolve_active_participant(request)
        if participant is None:
            return api_error_response(
                code="not_found",
                message=_NO_ACTIVE_EVENT,
                status_code=status.HTTP_404_NOT_FOUND,
                request=request,
            )
        return Response(ParticipantCurrentEventSerializer(projections.participant_current_event(participant)).data)


class ParticipantChallengeListView(APIView):
    """Return the participant-safe browse list of available challenges."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=ParticipantChallengeListItemSerializer(many=True))
    def get(self, request: Request) -> Response:
        """Return available challenges with this participant's solve state."""
        participant = _resolve_active_participant(request)
        if participant is None:
            return api_error_response(
                code="not_found",
                message=_NO_ACTIVE_EVENT,
                status_code=status.HTTP_404_NOT_FOUND,
                request=request,
            )
        data = projections.participant_challenge_list(participant)
        return Response(ParticipantChallengeListItemSerializer(data, many=True).data)


class ParticipantTeamView(APIView):
    """Return the participant's own team and members, or 404 when unteamed."""

    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = _PLAY_READ

    @extend_schema(responses=ParticipantTeamSerializer)
    def get(self, request: Request) -> Response:
        """Return the participant's team projection, or 404 for solo/unteamed."""
        participant = _resolve_active_participant(request)
        if participant is None:
            return api_error_response(
                code="not_found",
                message=_NO_ACTIVE_EVENT,
                status_code=status.HTTP_404_NOT_FOUND,
                request=request,
            )
        team = projections.participant_team(participant)
        if team is None:
            return api_error_response(
                code="not_found",
                message="Participant is not on a team.",
                status_code=status.HTTP_404_NOT_FOUND,
                request=request,
            )
        return Response(ParticipantTeamSerializer(team).data)
