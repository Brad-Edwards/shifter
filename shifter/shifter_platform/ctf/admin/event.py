"""Admin for CTF events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.db.models import Count

from ctf.admin._shared import (
    CTFAwardInline,
    CTFChallengeInline,
    CTFParticipantInline,
    CTFScheduledTaskInline,
    CTFTeamInline,
    SoftDeleteAdminMixin,
)
from ctf.models import (
    CTFEvent,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest


@admin.register(CTFEvent)
class CTFEventAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF events."""

    list_display = [
        "name",
        "status",
        "event_start",
        "event_end",
        "participant_count_display",
        "challenge_count_display",
        "team_mode",
        "created_by",
        "is_deleted_display",
    ]
    list_filter = ["status", "team_mode", "auto_cleanup", "deleted_at"]
    search_fields = ["name", "description", "created_by__email"]
    date_hierarchy = "event_start"
    ordering = ["-event_start"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "participant_count_display",
        "challenge_count_display",
    ]

    fieldsets = [
        (
            None,
            {
                "fields": ["name", "description", "created_by", "status"],
            },
        ),
        (
            "Schedule",
            {
                "fields": [
                    "event_start",
                    "event_end",
                    "registration_deadline",
                    "range_spinup_minutes",
                ],
            },
        ),
        (
            "Configuration",
            {
                "fields": [
                    "scenario_id",
                    "team_mode",
                    "team_size_limit",
                    "max_participants",
                    "scoring_mode",
                ],
            },
        ),
        (
            "Cleanup",
            {
                "fields": ["auto_cleanup", "cleanup_delay_hours"],
            },
        ),
        (
            "Metadata",
            {
                "fields": ["id", "created_at", "updated_at", "deleted_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    inlines = [CTFChallengeInline, CTFTeamInline, CTFParticipantInline, CTFAwardInline, CTFScheduledTaskInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        """Annotate queryset with counts."""
        qs = super().get_queryset(request)
        return qs.annotate(
            _participant_count=Count("participants", distinct=True),
            _challenge_count=Count("challenges", distinct=True),
        )

    @admin.display(description="Participants", ordering="_participant_count")
    def participant_count_display(self, obj: CTFEvent) -> int:
        """Display participant count."""
        return getattr(obj, "_participant_count", obj.participant_count)

    @admin.display(description="Challenges", ordering="_challenge_count")
    def challenge_count_display(self, obj: CTFEvent) -> int:
        """Display challenge count."""
        return getattr(obj, "_challenge_count", obj.challenge_count)

    @admin.display(description="Deleted", boolean=True)
    def is_deleted_display(self, obj: CTFEvent) -> bool:
        """Display soft delete status."""
        return obj.is_deleted
