"""Read-only operational escape hatch for workspace invitation diagnosis."""

from django.contrib import admin
from django.http import HttpRequest
from django.utils import timezone

from workspaces.models import WorkspaceInvitation


@admin.register(WorkspaceInvitation)
class WorkspaceInvitationAdmin(admin.ModelAdmin):
    """Superuser-only inspection; lifecycle mutations stay behind services."""

    fields = (
        "public_id",
        "workspace",
        "email",
        "role",
        "status",
        "expires_at",
        "created_by",
        "accepted_by",
        "accepted_at",
        "revoked_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields
    list_display = ("public_id", "workspace", "role", "status", "expires_at", "created_at")
    list_filter = ("role", "accepted_at", "revoked_at")
    ordering = ("-created_at",)
    actions = None

    @admin.display(description="Status")
    def status(self, invitation: WorkspaceInvitation) -> str:
        status = "pending"
        if invitation.accepted_at is not None:
            status = "accepted"
        elif invitation.revoked_at is not None:
            status = "revoked"
        elif invitation.expires_at <= timezone.now():
            status = "expired"
        return status

    def has_view_permission(self, request: HttpRequest, obj: WorkspaceInvitation | None = None) -> bool:
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: WorkspaceInvitation | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: WorkspaceInvitation | None = None) -> bool:
        return False
