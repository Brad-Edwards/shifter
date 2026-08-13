"""Read-only operational escape hatch for workspace invitation diagnosis."""

from django.contrib import admin
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
        if invitation.accepted_at is not None:
            return "accepted"
        if invitation.revoked_at is not None:
            return "revoked"
        if invitation.expires_at <= timezone.now():
            return "expired"
        return "pending"

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
