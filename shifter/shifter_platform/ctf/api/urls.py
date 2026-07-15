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
    path("challenges/<uuid:challenge_id>/submit/", views.api_submit_flag, name="api_submit_flag"),
    path("challenges/<uuid:challenge_id>/hint/", views.api_use_hint, name="api_use_hint"),
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
    path("submissions/", views.api_submissions, name="api_submissions"),
    path("events/<uuid:event_id>/participants/", views.api_participant_list, name="api_participant_list"),
    path("events/<uuid:event_id>/participants/import/", views.api_participant_import, name="api_participant_import"),
    path("participants/<uuid:participant_id>/", views.api_participant_detail, name="api_participant_detail"),
    path(
        "participants/<uuid:participant_id>/resend-invite/",
        views.api_participant_resend_invite,
        name="api_participant_resend_invite",
    ),
    path("range/status/", views.api_range_status, name="api_range_status"),
    path("range/access/", views.api_range_access, name="api_range_access"),
    path("events/<uuid:event_id>/ranges/", views.api_range_list, name="api_range_list"),
    path("events/<uuid:event_id>/ranges/provision/", views.api_provision_ranges, name="api_provision_ranges"),
    path("events/<uuid:event_id>/spares/", views.api_provision_event_spares, name="api_provision_event_spares"),
    path(
        "participants/<uuid:participant_id>/range/provision/",
        views.api_provision_participant_range,
        name="api_provision_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/destroy/",
        views.api_destroy_participant_range,
        name="api_destroy_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/stop/",
        views.api_stop_participant_range,
        name="api_stop_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/start/",
        views.api_start_participant_range,
        name="api_start_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/restart/",
        views.api_restart_participant_range,
        name="api_restart_participant_range",
    ),
    path(
        "participants/<uuid:participant_id>/range/recover/",
        views.api_recover_participant_range,
        name="api_recover_participant_range",
    ),
    path("participants/<uuid:participant_id>/bracket/", views.api_assign_bracket, name="api_assign_bracket"),
    path("events/<uuid:event_id>/scoreboard/", views.api_scoreboard, name="api_scoreboard"),
    path(
        "participants/<uuid:participant_id>/score-timeline/",
        views.api_score_timeline,
        name="api_score_timeline",
    ),
    path("events/<uuid:event_id>/notifications/", views.api_notification_list, name="api_notification_list"),
    path("notifications/<uuid:notification_id>/send/", views.api_notification_send, name="api_notification_send"),
    path(
        "events/<uuid:event_id>/email-templates/<str:notification_type>/",
        views.api_event_email_template,
        name="api_event_email_template",
    ),
    path("events/<uuid:event_id>/invitations/send/", views.api_send_invitations, name="api_send_invitations"),
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
