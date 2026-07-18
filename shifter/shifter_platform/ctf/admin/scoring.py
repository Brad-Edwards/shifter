"""Admin for CTF submissions and awards."""

from __future__ import annotations

from django.contrib import admin

from ctf.admin._shared import (
    SoftDeleteAdminMixin,
)
from ctf.models import (
    CTFAward,
    CTFSubmission,
)


@admin.register(CTFSubmission)
class CTFSubmissionAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF submissions."""

    list_display = [
        "participant",
        "challenge",
        "is_correct_display",
        "points_awarded",
        "attempt_number",
        "submitted_at",
        "ip_address",
    ]
    list_filter = ["is_correct", "challenge__event", "submitted_at"]
    search_fields = [
        "participant__name",
        "participant__email",
        "challenge__name",
        "submitted_flag",
    ]
    ordering = ["-submitted_at"]
    readonly_fields = ["id", "created_at", "updated_at", "deleted_at", "submitted_at"]
    date_hierarchy = "submitted_at"

    fieldsets = [
        (
            None,
            {
                "fields": ["participant", "challenge"],
            },
        ),
        (
            "Submission",
            {
                "fields": [
                    "submitted_flag",
                    "is_correct",
                    "points_awarded",
                    "attempt_number",
                ],
            },
        ),
        (
            "Details",
            {
                "fields": ["ip_address", "submitted_at"],
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

    @admin.display(description="Correct", boolean=True)
    def is_correct_display(self, obj: CTFSubmission) -> bool:
        """Display correctness as icon."""
        return obj.is_correct


@admin.register(CTFAward)
class CTFAwardAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF awards."""

    list_display = [
        "participant",
        "event",
        "points",
        "reason_short",
        "granted_by",
        "created_at",
        "is_deleted_display",
    ]
    list_filter = ["event", "granted_by", "deleted_at"]
    search_fields = ["participant__name", "participant__email", "reason", "event__name"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at", "deleted_at"]

    fieldsets = [
        (
            None,
            {
                "fields": ["event", "participant", "points", "reason", "granted_by"],
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

    @admin.display(description="Reason")
    def reason_short(self, obj: CTFAward) -> str:
        """Display truncated reason."""
        return obj.reason[:80] + "..." if len(obj.reason) > 80 else obj.reason

    @admin.display(description="Deleted", boolean=True)
    def is_deleted_display(self, obj: CTFAward) -> bool:
        """Display soft delete status."""
        return obj.is_deleted
