"""Explicit serializers for the workspace membership and organization APIs."""

from rest_framework import serializers

from workspaces.roles import WorkspaceRole


class OrganizationRefSerializer(serializers.Serializer):
    """Public organization projection (uuid + display name only)."""

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class OrganizationProfileSerializer(serializers.Serializer):
    """Read-only organization profile projection (ADR-048, PLAT-232).

    Emits the public ``uuid`` only; the internal integer primary key never
    appears on the wire.
    """

    uuid = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    support_email = serializers.EmailField(read_only=True)
    support_url = serializers.URLField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class OrganizationProfileUpdateSerializer(serializers.Serializer):
    """Partial-update command for the organization profile (PATCH mask).

    Every field is optional; an absent field is unchanged and an empty string
    clears an optional field. Unknown fields are rejected rather than ignored,
    and ``uuid``/timestamps are never writable here. The serializer owns HTTP
    shape (lengths, primitive formats); ``workspaces.services`` owns authority
    and persistence invariants.
    """

    name = serializers.CharField(max_length=200, allow_blank=False, required=False)
    description = serializers.CharField(max_length=2000, allow_blank=True, required=False)
    support_email = serializers.EmailField(max_length=254, allow_blank=True, required=False)
    support_url = serializers.URLField(max_length=500, allow_blank=True, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Reject unknown fields so a stale or hostile client cannot smuggle keys."""
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(dict.fromkeys(sorted(unknown), "Unknown field."))
        return attrs


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
