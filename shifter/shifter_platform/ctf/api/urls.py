"""Canonical /api/v1 CTF API routes."""

from __future__ import annotations

from django.urls import path

from ctf.api import organizer, participant_views, views

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
    path("events/", organizer.EventListView.as_view(), name="api_event_list"),
    path("events/<uuid:event_id>/", organizer.EventDetailView.as_view(), name="api_event_detail"),
    path(
        "events/<uuid:event_id>/force-delete/",
        organizer.ForceDeleteEventView.as_view(),
        name="api_force_delete_event",
    ),
    path("scenarios/", organizer.ScenarioListView.as_view(), name="api_scenarios"),
    path(
        "events/<uuid:event_id>/challenges/",
        organizer.ChallengeListView.as_view(),
        name="api_challenge_list",
    ),
    path(
        "challenges/<uuid:challenge_id>/",
        organizer.ChallengeDetailView.as_view(),
        name="api_challenge_detail",
    ),
    path("challenges/<uuid:challenge_id>/submit/", organizer.SubmitFlagView.as_view(), name="api_submit_flag"),
    path("challenges/<uuid:challenge_id>/hint/", organizer.UseHintView.as_view(), name="api_use_hint"),
    path(
        "challenges/<uuid:challenge_id>/hints/",
        organizer.ChallengeHintsView.as_view(),
        name="api_challenge_hints",
    ),
    path("hints/<uuid:hint_id>/delete/", organizer.HintDeleteView.as_view(), name="api_hint_delete"),
    path(
        "challenges/<uuid:challenge_id>/rate/",
        organizer.RateChallengeView.as_view(),
        name="api_rate_challenge",
    ),
    path("submissions/", organizer.SubmissionListView.as_view(), name="api_submissions"),
    path(
        "events/<uuid:event_id>/participants/",
        organizer.ParticipantListView.as_view(),
        name="api_participant_list",
    ),
    path(
        "events/<uuid:event_id>/participants/import/",
        organizer.ParticipantImportView.as_view(),
        name="api_participant_import",
    ),
    path(
        "participants/<uuid:participant_id>/",
        organizer.ParticipantDetailView.as_view(),
        name="api_participant_detail",
    ),
    path(
        "participants/<uuid:participant_id>/awards/",
        organizer.ParticipantAwardsView.as_view(),
        name="api_participant_awards",
    ),
    path(
        "awards/<uuid:award_id>/delete/",
        organizer.AwardRevokeView.as_view(),
        name="api_award_delete",
    ),
    path(
        "participants/<uuid:participant_id>/resend-invite/",
        organizer.ParticipantResendInviteView.as_view(),
        name="api_participant_resend_invite",
    ),
    path("range/status/", organizer.ParticipantRangeStatusView.as_view(), name="api_range_status"),
    path("range/access/", organizer.ParticipantRangeAccessView.as_view(), name="api_range_access"),
    path(
        "range/vpn-profile/",
        organizer.ParticipantVpnProfileView.as_view(),
        name="api_range_vpn_profile",
    ),
    path("events/<uuid:event_id>/ranges/", organizer.EventRangeListView.as_view(), name="api_range_list"),
    path(
        "events/<uuid:event_id>/ranges/provision/",
        organizer.EventRangeProvisionView.as_view(),
        name="api_provision_ranges",
    ),
    path(
        "events/<uuid:event_id>/spares/",
        organizer.EventSpareProvisionView.as_view(),
        name="api_provision_event_spares",
    ),
    path(
        "participants/<uuid:participant_id>/range/provision/",
        organizer.ParticipantRangeProvisionView.as_view(),
        name="api_provision_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/destroy/",
        organizer.ParticipantRangeDestroyView.as_view(),
        name="api_destroy_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/stop/",
        organizer.ParticipantRangeStopView.as_view(),
        name="api_stop_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/start/",
        organizer.ParticipantRangeStartView.as_view(),
        name="api_start_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/restart/",
        organizer.ParticipantRangeRestartView.as_view(),
        name="api_restart_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/recover/",
        organizer.ParticipantRangeRecoverView.as_view(),
        name="api_recover_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/bracket/",
        organizer.AssignBracketView.as_view(),
        name="api_assign_bracket",
    ),
    path("events/<uuid:event_id>/scoreboard/", views.api_scoreboard, name="api_scoreboard"),
    path(
        "events/<uuid:event_id>/organizer-scoreboard/",
        organizer.OrganizerScoreboardView.as_view(),
        name="api_organizer_scoreboard",
    ),
    path(
        "participants/<uuid:participant_id>/score-timeline/",
        organizer.ScoreTimelineView.as_view(),
        name="api_score_timeline",
    ),
    path(
        "events/<uuid:event_id>/notifications/",
        organizer.NotificationListView.as_view(),
        name="api_notification_list",
    ),
    path(
        "notifications/<uuid:notification_id>/send/",
        organizer.NotificationSendView.as_view(),
        name="api_notification_send",
    ),
    path(
        "events/<uuid:event_id>/email-templates/<str:notification_type>/",
        organizer.EventEmailTemplateView.as_view(),
        name="api_event_email_template",
    ),
    path(
        "events/<uuid:event_id>/invitations/send/",
        organizer.SendInvitationsView.as_view(),
        name="api_send_invitations",
    ),
    path(
        "challenges/<uuid:challenge_id>/flags/add/",
        organizer.AddFlagView.as_view(),
        name="api_add_flag",
    ),
    path("flags/<uuid:flag_id>/remove/", organizer.RemoveFlagView.as_view(), name="api_remove_flag"),
    path(
        "challenges/<uuid:challenge_id>/files/",
        organizer.ChallengeFilesView.as_view(),
        name="api_challenge_files",
    ),
    path(
        "files/<uuid:file_id>/delete/",
        organizer.ChallengeFileDeleteView.as_view(),
        name="api_challenge_file_delete",
    ),
    path("files/<uuid:file_id>/download/", organizer.FileDownloadView.as_view(), name="api_file_download"),
    path(
        "challenges/<uuid:challenge_id>/prerequisites/",
        organizer.ChallengePrerequisitesView.as_view(),
        name="api_challenge_prerequisites",
    ),
    path(
        "prerequisites/<uuid:prerequisite_id>/delete/",
        organizer.PrerequisiteDeleteView.as_view(),
        name="api_prerequisite_delete",
    ),
]
