"""Integration tests for the canonical participant CTF API (``/api/v1/ctf/me/*``).

These exercise the typed participant read surface end-to-end through the DRF
stack (permissions, event-scoped participant resolution, projections). They
assert both the happy-path shape and the participant-safety invariant: flag
hashes, flag formats, and solutions never appear in the participant projection.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ctf.enums import ChallengeCategory, ChallengeDifficulty, ParticipantStatus
from ctf.models import CTFChallenge, CTFParticipant, CTFSubmission

from ._api_flow_helpers import call_json


def _activate(user, event) -> None:
    """Point the user's profile at ``event`` as their active CTF event."""
    from management.services import set_active_ctf_event

    set_active_ctf_event(user, event.pk)


class TestParticipantCurrentEvent:
    """``GET /api/v1/ctf/me/event/``."""

    def test_returns_current_event_and_self_state(
        self, authenticated_participant_client, ctf_participant, participant_user, ctf_event
    ):
        """A participant sees their event plus their own participant state."""
        _activate(participant_user, ctf_event)

        response = call_json(authenticated_participant_client, "get", "api_participant_current_event")

        assert response.status_code == 200
        body = response.json()
        assert body["event"]["id"] == str(ctf_event.id)
        assert body["event"]["name"] == ctf_event.name
        assert body["participant"]["id"] == str(ctf_participant.id)
        assert body["participant"]["cached_solve_count"] == 0

    def test_404_when_no_active_event(self, authenticated_participant_client, ctf_participant, participant_user):
        """A participant with no active event selected gets a 404 envelope."""
        response = call_json(authenticated_participant_client, "get", "api_participant_current_event")

        assert response.status_code == 404

    def test_forbidden_for_non_participant(self, authenticated_organizer_client):
        """An organizer (not a participant) is rejected."""
        response = call_json(authenticated_organizer_client, "get", "api_participant_current_event")

        assert response.status_code == 403


class TestParticipantChallengeList:
    """``GET /api/v1/ctf/me/challenges/``."""

    def test_lists_available_challenges_with_solve_state(
        self, authenticated_participant_client, ctf_participant, participant_user, ctf_event, ctf_challenge
    ):
        """The browse list carries solve state and never leaks flag/solution."""
        _activate(participant_user, ctf_event)
        CTFSubmission.objects.create(
            participant=ctf_participant,
            challenge=ctf_challenge,
            submitted_flag="FLAG{correct}",
            is_correct=True,
            points_awarded=ctf_challenge.points,
            attempt_number=1,
            ip_address="192.168.1.1",
        )

        response = call_json(authenticated_participant_client, "get", "api_participant_challenges")

        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == str(ctf_challenge.id)
        assert item["solved"] is True
        assert "flag_hash" not in item
        assert "flag_format" not in item
        assert "solution" not in item

    def test_excludes_hidden_and_unreleased(
        self, authenticated_participant_client, ctf_participant, participant_user, ctf_event, ctf_challenge
    ):
        """Hidden and not-yet-released challenges are filtered server-side."""
        _activate(participant_user, ctf_event)
        CTFChallenge.objects.create(
            event=ctf_event,
            name="Hidden Challenge",
            description="secret",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="$2b$12$hidden_placeholder",
            visibility="hidden",
        )
        CTFChallenge.objects.create(
            event=ctf_event,
            name="Future Challenge",
            description="later",
            category=ChallengeCategory.WEB.value,
            points=100,
            difficulty=ChallengeDifficulty.EASY.value,
            flag_hash="$2b$12$future_placeholder",
            release_time=timezone.now() + timedelta(days=1),
        )

        response = call_json(authenticated_participant_client, "get", "api_participant_challenges")

        assert response.status_code == 200
        names = {item["name"] for item in response.json()}
        assert names == {ctf_challenge.name}


class TestParticipantTeam:
    """``GET /api/v1/ctf/me/team/``."""

    def test_returns_team_and_members(
        self, authenticated_participant_client, ctf_participant_team, participant_user, ctf_event_team, ctf_team
    ):
        """A teamed participant sees their team and teammate names."""
        _activate(participant_user, ctf_event_team)
        CTFParticipant.objects.create(
            event=ctf_event_team,
            email="teammate@test.com",
            name="Teammate",
            team=ctf_team,
            status=ParticipantStatus.ACTIVE.value,
            registered_at=timezone.now(),
        )

        response = call_json(authenticated_participant_client, "get", "api_participant_team")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(ctf_team.id)
        member_names = {member["name"] for member in body["members"]}
        assert {"Team Participant", "Teammate"} <= member_names

    def test_404_when_solo(self, authenticated_participant_client, ctf_participant, participant_user, ctf_event):
        """A participant not on a team gets a 404 envelope."""
        _activate(participant_user, ctf_event)

        response = call_json(authenticated_participant_client, "get", "api_participant_team")

        assert response.status_code == 404
