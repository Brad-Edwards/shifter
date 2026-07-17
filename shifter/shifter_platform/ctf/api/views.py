"""Canonical DRF views for the CTF JSON API."""

from __future__ import annotations

from typing import Any

from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView

from ctf.api._base import _canonical_error_response
from ctf.api.serializers import PublicScoreboardResponseSerializer


class PublicScoreboardView(APIView):
    """Public event scoreboard read surface."""

    versioning_class = None
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=PublicScoreboardResponseSerializer)
    def get(self, request: Request, event_id: Any) -> JsonResponse:
        """Return the public scoreboard payload for an event."""
        from ctf.exceptions import CTFNotFoundError
        from ctf.services import get_event
        from ctf.services.scoring import get_scoreboard, get_team_scoreboard
        from ctf.views import _parsing

        try:
            event = get_event(event_id)
        except CTFNotFoundError:
            response = JsonResponse({"error": "Event not found"}, status=404)
            return _canonical_error_response(request, response) or response

        if not event.scoreboard_visible:
            return JsonResponse({"scoreboard_hidden": True})

        freeze_at = event.scoreboard_freeze_at if event.is_scoreboard_frozen else None
        bracket_param = request.query_params.get("bracket")
        brackets, _selected_bracket, bracket_id = _parsing._resolve_bracket_filter(event.id, bracket_param)
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

        return JsonResponse(
            {
                "event_id": str(event.id),
                "team_mode": event.team_mode,
                "frozen": event.is_scoreboard_frozen,
                "rankings": rankings,
                "bracket_rankings": bracket_rankings,
                "brackets": [{"id": str(bracket.id), "name": bracket.name} for bracket in brackets],
            }
        )


api_scoreboard = PublicScoreboardView.as_view()

__all__ = [
    "api_scoreboard",
]
