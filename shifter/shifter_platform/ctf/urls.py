"""CTF URL configuration.

Defines URL patterns for:
- CTF Admin/Organizer views (/ctf/admin/...)
- CTF Participant views (/ctf/...)
- CTF scoreboard JSON endpoint (/ctf/api/events/<id>/scoreboard/)

The legacy ``/ctf/api/*`` JSON API was retired (issue #1328): the UI and all
callers use the canonical ``/api/v1/ctf/`` DRF routes (see ``ctf.api.urls``).
The single scoreboard endpoint is intentionally retained on the legacy route
because the v1 scoreboard (``v1:ctf:api_scoreboard`` -> ``PublicScoreboardView``)
has different access semantics than the participant scoreboard page requires.
"""

from __future__ import annotations

from django.urls import path

from ctf import views

app_name = "ctf"

# -----------------------------------------------------------------------------
# Participant URLs (CTF Competitors)
# -----------------------------------------------------------------------------
participant_patterns = [
    # Dashboard
    path("", views.participant_dashboard, name="participant_dashboard"),
    path("login/", views.ctf_login, name="ctf_login"),
    path("change-password/", views.ctf_change_password, name="ctf_change_password"),
    path("event/", views.participant_event, name="participant_event"),
    # Challenges
    path("challenges/", views.participant_challenges, name="challenges"),
    path("challenges/<uuid:challenge_id>/", views.challenge_detail, name="challenge_detail"),
    # Range
    path("range/", views.participant_range, name="participant_range"),
    # Scoreboard
    path("scoreboard/", views.scoreboard, name="scoreboard"),
    path(
        "participants/<uuid:participant_id>/solves/",
        views.participant_solve_history,
        name="participant_solve_history",
    ),
    # Team
    path("team/", views.participant_team, name="participant_team"),
    path("team/join/", views.team_join, name="team_join"),
    # Help
    path("help/", views.ctf_help, name="ctf_help"),
]

# -----------------------------------------------------------------------------
# Admin/Organizer URLs
# -----------------------------------------------------------------------------
admin_patterns = [
    # Dashboard
    path("admin/", views.admin_dashboard, name="admin_dashboard"),
    # Events
    path("admin/events/", views.admin_event_list, name="admin_event_list"),
    path("admin/events/create/", views.admin_event_create, name="admin_event_create"),
    path("admin/events/<uuid:event_id>/", views.admin_event_detail, name="admin_event_detail"),
    path("admin/events/<uuid:event_id>/edit/", views.admin_event_edit, name="admin_event_edit"),
    path(
        "admin/events/<uuid:event_id>/force-delete/",
        views.admin_event_force_delete,
        name="admin_event_force_delete",
    ),
    # Challenges
    path(
        "admin/events/<uuid:event_id>/challenges/",
        views.admin_challenge_list,
        name="admin_challenge_list",
    ),
    path(
        "admin/events/<uuid:event_id>/challenges/create/",
        views.admin_challenge_create,
        name="admin_challenge_create",
    ),
    path(
        "admin/challenges/<uuid:challenge_id>/",
        views.admin_challenge_detail,
        name="admin_challenge_detail",
    ),
    path(
        "admin/challenges/<uuid:challenge_id>/edit/",
        views.admin_challenge_edit,
        name="admin_challenge_edit",
    ),
    # Participants
    path(
        "admin/events/<uuid:event_id>/participants/",
        views.admin_participant_list,
        name="admin_participant_list",
    ),
    path(
        "admin/events/<uuid:event_id>/participants/import/",
        views.admin_participant_import,
        name="admin_participant_import",
    ),
    path(
        "admin/events/<uuid:event_id>/participants/generate/",
        views.admin_participant_batch,
        name="admin_participant_batch",
    ),
    path(
        "admin/events/<uuid:event_id>/participants/add/",
        views.admin_participant_add,
        name="admin_participant_add",
    ),
    path(
        "admin/participants/<uuid:participant_id>/",
        views.admin_participant_detail,
        name="admin_participant_detail",
    ),
    path(
        "admin/participants/<uuid:participant_id>/rename/",
        views.admin_participant_rename,
        name="admin_participant_rename",
    ),
    path(
        "admin/participants/<uuid:participant_id>/delivery-email/",
        views.admin_participant_email,
        name="admin_participant_email",
    ),
    # Teams
    path("admin/events/<uuid:event_id>/teams/", views.admin_team_list, name="admin_team_list"),
    # Scoreboard
    path(
        "admin/events/<uuid:event_id>/scoreboard/",
        views.admin_scoreboard,
        name="admin_scoreboard",
    ),
    # Brackets
    path(
        "admin/events/<uuid:event_id>/brackets/",
        views.admin_bracket_list,
        name="admin_bracket_list",
    ),
    path(
        "admin/events/<uuid:event_id>/brackets/create/",
        views.admin_bracket_create,
        name="admin_bracket_create",
    ),
    path(
        "admin/brackets/<uuid:bracket_id>/edit/",
        views.admin_bracket_edit,
        name="admin_bracket_edit",
    ),
    path(
        "admin/brackets/<uuid:bracket_id>/delete/",
        views.admin_bracket_delete,
        name="admin_bracket_delete",
    ),
    # Ranges
    path("admin/events/<uuid:event_id>/ranges/", views.admin_range_list, name="admin_range_list"),
    # Notifications
    path(
        "admin/events/<uuid:event_id>/notifications/",
        views.admin_notification_list,
        name="admin_notification_list",
    ),
    path(
        "admin/events/<uuid:event_id>/notifications/create/",
        views.admin_notification_create,
        name="admin_notification_create",
    ),
    # Email Templates
    path(
        "admin/events/<uuid:event_id>/email-templates/",
        views.admin_event_email_templates,
        name="admin_event_email_templates",
    ),
    # Analytics
    path(
        "admin/events/<uuid:event_id>/analytics/",
        views.admin_analytics,
        name="admin_analytics",
    ),
    # Challenge file upload (from admin detail page)
    path(
        "admin/challenges/<uuid:challenge_id>/upload/",
        views.admin_challenge_file_upload,
        name="admin_challenge_file_upload",
    ),
]

# -----------------------------------------------------------------------------
# API URLs
# -----------------------------------------------------------------------------
api_patterns = [
    # Scoreboard API: intentionally retained on the legacy /ctf/api/ route (#1328).
    # Every other legacy /ctf/api/* endpoint was retired in favour of /api/v1/ctf/.
    path(
        "api/events/<uuid:event_id>/scoreboard/",
        views.api_scoreboard,
        name="api_scoreboard",
    ),
]

# Combine all patterns
urlpatterns = participant_patterns + admin_patterns + api_patterns
