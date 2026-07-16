"""Canonical /api/v1 CTF API routes."""

from __future__ import annotations

from django.urls import path

from ctf.api import organizer_views, participant_views, views

app_name = "ctf"

urlpatterns = [
    # Participant self-reads (typed DRF projections for the SPA workspace).
    path("me/event/", participant_views.ParticipantCurrentEventView.as_view(), name="api_participant_current_event"),
    path("me/challenges/", participant_views.ParticipantChallengeListView.as_view(), name="api_participant_challenges"),
    path(
        "me/challenges/<uuid:challenge_id>/",
        participant_views.ParticipantChallengeDetailView.as_view(),
        name="api_participant_challenge_detail",
    ),
    path("me/team/", participant_views.ParticipantTeamView.as_view(), name="api_participant_team"),
    path("events/", organizer_views.EventListView.as_view(), name="api_event_list"),
    path("events/<uuid:event_id>/", organizer_views.EventDetailView.as_view(), name="api_event_detail"),
    path(
        "events/<uuid:event_id>/force-delete/",
        organizer_views.ForceDeleteEventView.as_view(),
        name="api_force_delete_event",
    ),
    path("scenarios/", organizer_views.ScenarioListView.as_view(), name="api_scenarios"),
    path(
        "events/<uuid:event_id>/challenges/",
        organizer_views.ChallengeListView.as_view(),
        name="api_challenge_list",
    ),
    path(
        "challenges/<uuid:challenge_id>/",
        organizer_views.ChallengeDetailView.as_view(),
        name="api_challenge_detail",
    ),
    path("challenges/<uuid:challenge_id>/submit/", organizer_views.SubmitFlagView.as_view(), name="api_submit_flag"),
    path("challenges/<uuid:challenge_id>/hint/", organizer_views.UseHintView.as_view(), name="api_use_hint"),
    path(
        "challenges/<uuid:challenge_id>/hints/",
        organizer_views.ChallengeHintsView.as_view(),
        name="api_challenge_hints",
    ),
    path("hints/<uuid:hint_id>/delete/", organizer_views.HintDeleteView.as_view(), name="api_hint_delete"),
    path(
        "challenges/<uuid:challenge_id>/rate/",
        organizer_views.RateChallengeView.as_view(),
        name="api_rate_challenge",
    ),
    path("submissions/", organizer_views.SubmissionListView.as_view(), name="api_submissions"),
    path(
        "events/<uuid:event_id>/participants/",
        organizer_views.ParticipantListView.as_view(),
        name="api_participant_list",
    ),
    path(
        "events/<uuid:event_id>/participants/import/",
        organizer_views.ParticipantImportView.as_view(),
        name="api_participant_import",
    ),
    path(
        "participants/<uuid:participant_id>/",
        organizer_views.ParticipantDetailView.as_view(),
        name="api_participant_detail",
    ),
    path(
        "participants/<uuid:participant_id>/resend-invite/",
        organizer_views.ParticipantResendInviteView.as_view(),
        name="api_participant_resend_invite",
    ),
    path("range/status/", organizer_views.ParticipantRangeStatusView.as_view(), name="api_range_status"),
    path("range/access/", organizer_views.ParticipantRangeAccessView.as_view(), name="api_range_access"),
    path("events/<uuid:event_id>/ranges/", organizer_views.EventRangeListView.as_view(), name="api_range_list"),
    path(
        "events/<uuid:event_id>/ranges/provision/",
        organizer_views.EventRangeProvisionView.as_view(),
        name="api_provision_ranges",
    ),
    path(
        "events/<uuid:event_id>/spares/",
        organizer_views.EventSpareProvisionView.as_view(),
        name="api_provision_event_spares",
    ),
    path(
        "participants/<uuid:participant_id>/range/provision/",
        organizer_views.ParticipantRangeProvisionView.as_view(),
        name="api_provision_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/destroy/",
        organizer_views.ParticipantRangeDestroyView.as_view(),
        name="api_destroy_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/stop/",
        organizer_views.ParticipantRangeStopView.as_view(),
        name="api_stop_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/start/",
        organizer_views.ParticipantRangeStartView.as_view(),
        name="api_start_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/restart/",
        organizer_views.ParticipantRangeRestartView.as_view(),
        name="api_restart_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/recover/",
        organizer_views.ParticipantRangeRecoverView.as_view(),
        name="api_recover_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/bracket/",
        organizer_views.AssignBracketView.as_view(),
        name="api_assign_bracket",
    ),
    path("events/<uuid:event_id>/scoreboard/", views.api_scoreboard, name="api_scoreboard"),
    path(
        "events/<uuid:event_id>/organizer-scoreboard/",
        organizer_views.OrganizerScoreboardView.as_view(),
        name="api_organizer_scoreboard",
    ),
    path(
        "participants/<uuid:participant_id>/score-timeline/",
        organizer_views.ScoreTimelineView.as_view(),
        name="api_score_timeline",
    ),
    path(
        "events/<uuid:event_id>/notifications/",
        organizer_views.NotificationListView.as_view(),
        name="api_notification_list",
    ),
    path(
        "notifications/<uuid:notification_id>/send/",
        organizer_views.NotificationSendView.as_view(),
        name="api_notification_send",
    ),
    path(
        "events/<uuid:event_id>/email-templates/<str:notification_type>/",
        organizer_views.EventEmailTemplateView.as_view(),
        name="api_event_email_template",
    ),
    path(
        "events/<uuid:event_id>/invitations/send/",
        organizer_views.SendInvitationsView.as_view(),
        name="api_send_invitations",
    ),
    path(
        "challenges/<uuid:challenge_id>/flags/add/",
        organizer_views.AddFlagView.as_view(),
        name="api_add_flag",
    ),
    path("flags/<uuid:flag_id>/remove/", organizer_views.RemoveFlagView.as_view(), name="api_remove_flag"),
    path(
        "challenges/<uuid:challenge_id>/files/",
        organizer_views.ChallengeFilesView.as_view(),
        name="api_challenge_files",
    ),
    path(
        "files/<uuid:file_id>/delete/",
        organizer_views.ChallengeFileDeleteView.as_view(),
        name="api_challenge_file_delete",
    ),
    path("files/<uuid:file_id>/download/", organizer_views.FileDownloadView.as_view(), name="api_file_download"),
    path(
        "challenges/<uuid:challenge_id>/prerequisites/",
        organizer_views.ChallengePrerequisitesView.as_view(),
        name="api_challenge_prerequisites",
    ),
    path(
        "prerequisites/<uuid:prerequisite_id>/delete/",
        organizer_views.PrerequisiteDeleteView.as_view(),
        name="api_prerequisite_delete",
    ),
]
