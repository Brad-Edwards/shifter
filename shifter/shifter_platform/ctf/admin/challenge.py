"""Admin for CTF challenges, files, and prerequisites."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import admin
from django.db.models import Count, Q

from ctf.admin._shared import (
    CTFChallengeFileInline,
    CTFChallengePrerequisiteInline,
    CTFSubmissionInline,
    SoftDeleteAdminMixin,
)
from ctf.models import (
    CTFChallenge,
    CTFChallengeFile,
    CTFChallengePrerequisite,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest


@admin.register(CTFChallenge)
class CTFChallengeAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF challenges."""

    list_display = [
        "name",
        "event",
        "category",
        "points",
        "difficulty",
        "solve_count_display",
        "is_released",
        "order",
        "is_deleted_display",
    ]
    list_filter = ["category", "difficulty", "event", "deleted_at"]
    search_fields = ["name", "description", "event__name"]
    ordering = ["event", "category", "order"]
    readonly_fields = ["id", "created_at", "updated_at", "deleted_at", "solve_count_display"]

    fieldsets = [
        (
            None,
            {
                "fields": ["event", "name", "description"],
            },
        ),
        (
            "Challenge Details",
            {
                "fields": ["category", "points", "difficulty", "order"],
            },
        ),
        (
            "Flag",
            {
                "fields": ["flag_hash", "flag_format"],
            },
        ),
        (
            "Limits",
            {
                "fields": ["max_attempts", "release_time"],
            },
        ),
        (
            "Connection",
            {
                "fields": ["target_instance_name", "target_port"],
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

    inlines = [CTFChallengeFileInline, CTFChallengePrerequisiteInline, CTFSubmissionInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        """Annotate queryset with solve count."""
        qs = super().get_queryset(request)
        return qs.annotate(_solve_count=Count("submissions", filter=Q(submissions__is_correct=True)))

    @admin.display(description="Solves", ordering="_solve_count")
    def solve_count_display(self, obj: CTFChallenge) -> int:
        """Display solve count."""
        return getattr(obj, "_solve_count", obj.solve_count)

    @admin.display(description="Deleted", boolean=True)
    def is_deleted_display(self, obj: CTFChallenge) -> bool:
        """Display soft delete status."""
        return obj.is_deleted


@admin.register(CTFChallengeFile)
class CTFChallengeFileAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF challenge files."""

    list_display = [
        "filename",
        "display_name",
        "challenge",
        "file_size_bytes",
        "content_type",
        "order",
        "is_deleted_display",
    ]
    list_filter = ["content_type", "challenge__event", "deleted_at"]
    search_fields = ["filename", "display_name", "challenge__name"]
    ordering = ["challenge", "order"]
    readonly_fields = ["id", "s3_key", "sha256_hash", "file_size_bytes", "created_at", "updated_at", "deleted_at"]

    fieldsets = [
        (
            None,
            {
                "fields": ["challenge", "filename", "display_name"],
            },
        ),
        (
            "File Details",
            {
                "fields": ["s3_key", "file_size_bytes", "content_type", "sha256_hash", "order"],
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

    @admin.display(description="Deleted", boolean=True)
    def is_deleted_display(self, obj: CTFChallengeFile) -> bool:
        """Display soft delete status."""
        return obj.is_deleted


@admin.register(CTFChallengePrerequisite)
class CTFChallengePrerequisiteAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Admin for CTF challenge prerequisites."""

    list_display = [
        "challenge",
        "required_challenge",
        "is_deleted_display",
    ]
    list_filter = ["challenge__event", "deleted_at"]
    search_fields = ["challenge__name", "required_challenge__name"]
    ordering = ["challenge"]
    readonly_fields = ["id", "created_at", "updated_at", "deleted_at"]

    fieldsets = [
        (
            None,
            {
                "fields": ["challenge", "required_challenge"],
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

    @admin.display(description="Deleted", boolean=True)
    def is_deleted_display(self, obj: CTFChallengePrerequisite) -> bool:
        """Display soft delete status."""
        return obj.is_deleted
