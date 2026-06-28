"""Canonical DRF views for the CTF JSON API."""

from __future__ import annotations

from typing import Any

from django.http import JsonResponse
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView

from ctf.api._base import (
    CTF_ORGANIZER_PERMISSIONS,
    CTF_PARTICIPANT_PERMISSIONS,
    CTF_ROLE_PERMISSIONS,
    CTFLegacyAPIView,
    _canonical_error_response,
)
from shared.api_tokens import scopes


class EventListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.events.api_event_list"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class EventDetailView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.events.api_event_detail"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ForceDeleteEventView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.events.api_force_delete_event"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ScenarioListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.events.api_scenarios"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)


class ChallengeListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.challenges.api_challenge_list"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ChallengeDetailView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.challenges.api_challenge_detail"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class SubmitFlagView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.play.api_submit_flag"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = (scopes.CTF_PLAY_WRITE,)


class UseHintView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.play.api_use_hint"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = (scopes.CTF_PLAY_WRITE,)


class RateChallengeView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.play.api_rate_challenge"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_write_scopes = (scopes.CTF_PLAY_WRITE,)


class SubmissionListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.play.api_submissions"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = (scopes.CTF_PLAY_READ,)


class ChallengeHintListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.hints.api_challenge_hints"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class HintDeleteView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.hints.api_hint_delete"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.participants.api_participant_list"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantImportView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.participants.api_participant_import"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantDetailView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.participants.api_participant_detail"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantResendInviteView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.participants.api_participant_resend_invite"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeStatusView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_range_status"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = (scopes.CTF_PLAY_READ,)


class ParticipantRangeAccessView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_range_access"
    permission_classes = CTF_PARTICIPANT_PERMISSIONS
    required_read_scopes = (scopes.CTF_PLAY_READ,)
    required_write_scopes = (scopes.CTF_PLAY_READ,)


class EventRangeListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_range_list"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)


class EventRangeProvisionView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_provision_ranges"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeProvisionView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_provision_participant_range"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeDestroyView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_destroy_participant_range"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeStopView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_stop_participant_range"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeStartView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_start_participant_range"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ParticipantRangeRestartView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_restart_participant_range"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class AssignBracketView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.admin_brackets.api_assign_bracket"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ScoreTimelineView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.scoreboard.api_score_timeline"
    permission_classes = CTF_ROLE_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ, scopes.CTF_PLAY_READ)


class NotificationListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.notifications.api_notification_list"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class NotificationSendView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.notifications.api_notification_send"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class EventEmailTemplateView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.notifications.api_event_email_template"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class SendInvitationsView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.ranges.api_send_invitations"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class AddFlagView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.flags.api_add_flag"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class RemoveFlagView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.flags.api_remove_flag"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ChallengeFileListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.files.api_challenge_files"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ChallengeFileDeleteView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.files.api_challenge_file_delete"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class ChallengeFileDownloadView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.files.api_file_download"
    permission_classes = CTF_ROLE_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ, scopes.CTF_PLAY_READ)


class ChallengePrerequisiteListView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.prerequisites.api_challenge_prerequisites"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_read_scopes = (scopes.CTF_EVENT_READ,)
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class PrerequisiteDeleteView(CTFLegacyAPIView):
    legacy_view_path = "ctf.views.api.prerequisites.api_prerequisite_delete"
    permission_classes = CTF_ORGANIZER_PERMISSIONS
    required_write_scopes = (scopes.CTF_EVENT_WRITE,)


class PublicScoreboardView(APIView):
    """Public event scoreboard read surface."""

    versioning_class = None
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request, event_id: Any) -> JsonResponse:
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


api_event_list = EventListView.as_view()
api_event_detail = EventDetailView.as_view()
api_force_delete_event = ForceDeleteEventView.as_view()
api_scenarios = ScenarioListView.as_view()
api_challenge_list = ChallengeListView.as_view()
api_challenge_detail = ChallengeDetailView.as_view()
api_submit_flag = SubmitFlagView.as_view()
api_use_hint = UseHintView.as_view()
api_challenge_hints = ChallengeHintListView.as_view()
api_hint_delete = HintDeleteView.as_view()
api_rate_challenge = RateChallengeView.as_view()
api_submissions = SubmissionListView.as_view()
api_participant_list = ParticipantListView.as_view()
api_participant_import = ParticipantImportView.as_view()
api_participant_detail = ParticipantDetailView.as_view()
api_participant_resend_invite = ParticipantResendInviteView.as_view()
api_range_status = ParticipantRangeStatusView.as_view()
api_range_access = ParticipantRangeAccessView.as_view()
api_range_list = EventRangeListView.as_view()
api_provision_ranges = EventRangeProvisionView.as_view()
api_provision_participant_range = ParticipantRangeProvisionView.as_view()
api_destroy_participant_range = ParticipantRangeDestroyView.as_view()
api_stop_participant_range = ParticipantRangeStopView.as_view()
api_start_participant_range = ParticipantRangeStartView.as_view()
api_restart_participant_range = ParticipantRangeRestartView.as_view()
api_assign_bracket = AssignBracketView.as_view()
api_scoreboard = PublicScoreboardView.as_view()
api_score_timeline = ScoreTimelineView.as_view()
api_notification_list = NotificationListView.as_view()
api_notification_send = NotificationSendView.as_view()
api_event_email_template = EventEmailTemplateView.as_view()
api_send_invitations = SendInvitationsView.as_view()
api_add_flag = AddFlagView.as_view()
api_remove_flag = RemoveFlagView.as_view()
api_challenge_files = ChallengeFileListView.as_view()
api_challenge_file_delete = ChallengeFileDeleteView.as_view()
api_file_download = ChallengeFileDownloadView.as_view()
api_challenge_prerequisites = ChallengePrerequisiteListView.as_view()
api_prerequisite_delete = PrerequisiteDeleteView.as_view()

__all__ = [
    "api_add_flag",
    "api_assign_bracket",
    "api_challenge_detail",
    "api_challenge_file_delete",
    "api_challenge_files",
    "api_challenge_hints",
    "api_challenge_list",
    "api_challenge_prerequisites",
    "api_destroy_participant_range",
    "api_event_detail",
    "api_event_email_template",
    "api_event_list",
    "api_file_download",
    "api_force_delete_event",
    "api_hint_delete",
    "api_notification_list",
    "api_notification_send",
    "api_participant_detail",
    "api_participant_import",
    "api_participant_list",
    "api_participant_resend_invite",
    "api_prerequisite_delete",
    "api_provision_participant_range",
    "api_provision_ranges",
    "api_range_access",
    "api_range_list",
    "api_range_status",
    "api_rate_challenge",
    "api_remove_flag",
    "api_restart_participant_range",
    "api_scenarios",
    "api_score_timeline",
    "api_scoreboard",
    "api_send_invitations",
    "api_start_participant_range",
    "api_stop_participant_range",
    "api_submissions",
    "api_submit_flag",
    "api_use_hint",
]
