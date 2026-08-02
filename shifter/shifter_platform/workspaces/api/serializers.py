"""Explicit serializers for the workspace membership API."""

from rest_framework import serializers

from workspaces.roles import WorkspaceRole


class OrganizationRefSerializer(serializers.Serializer):
    """Public organization projection (uuid + display name only)."""

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class PrincipalWorkspaceContextSerializer(serializers.Serializer):
    """One workspace the caller belongs to, with role and advisory capabilities.

    ``role`` and ``capabilities`` are display/presentation hints derived from the
    central role-to-operation policy; every resource endpoint still reauthorizes
    the operation it performs (ADR-046-R11).
    """

    organization = OrganizationRefSerializer(read_only=True)
    workspace_uuid = serializers.UUIDField(read_only=True)
    workspace_name = serializers.CharField(read_only=True)
    is_personal = serializers.BooleanField(read_only=True)
    role = serializers.ChoiceField(read_only=True, choices=WorkspaceRole.choices)
    capabilities = serializers.ListField(child=serializers.CharField(), read_only=True)


class WorkspaceMembershipSerializer(serializers.Serializer):
    """Minimum public membership projection."""

    membership_id = serializers.IntegerField(read_only=True)
    workspace_uuid = serializers.UUIDField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    role = serializers.ChoiceField(read_only=True, choices=WorkspaceRole.choices)
    created_at = serializers.DateTimeField(read_only=True)


class AddWorkspaceMemberSerializer(serializers.Serializer):
    """Add-existing-account command."""

    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=WorkspaceRole.choices)


class ChangeWorkspaceMemberRoleSerializer(serializers.Serializer):
    """Closed role-change command."""

    role = serializers.ChoiceField(choices=WorkspaceRole.choices)
