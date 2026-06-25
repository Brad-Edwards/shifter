"""Serializers for the Mission Control DRF API."""

from __future__ import annotations

from rest_framework import serializers

AGENT_TYPE_CHOICES = ("xdr", "xdr_collector", "cloud_identity_engine")


class LaunchRangeSerializer(serializers.Serializer):
    """Validate range launch requests."""

    agents = serializers.DictField(child=serializers.IntegerField(min_value=1), required=False)
    agent_id = serializers.IntegerField(required=False, allow_null=True)
    scenario = serializers.CharField(required=False, default="basic", allow_blank=False, trim_whitespace=True)

    def validate_agent_id(self, value: int | None) -> int:
        if not value:
            raise serializers.ValidationError("agent_id is required")
        return value

    def validate(self, attrs: dict) -> dict:
        if "agents" not in attrs and "agent_id" not in attrs:
            raise serializers.ValidationError("Either 'agents' or 'agent_id' is required")
        return attrs


class RangeLifecycleSerializer(serializers.Serializer):
    """Validate range cancel/destroy/pause/resume requests."""

    request_id = serializers.UUIDField(required=False)
    range_id = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs: dict) -> dict:
        if "request_id" not in attrs and "range_id" not in attrs:
            raise serializers.ValidationError("request_id or range_id is required")
        return attrs


class UploadInitiateSerializer(serializers.Serializer):
    """Validate agent-upload initiation requests."""

    name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    filename = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    file_size = serializers.JSONField(required=False)
    agent_type = serializers.CharField(required=False, allow_blank=True, default="xdr", trim_whitespace=True)

    def validate(self, attrs: dict) -> dict:
        name = attrs.get("name", "")
        filename = attrs.get("filename", "")
        file_size = attrs.get("file_size", 0)
        agent_type = attrs.get("agent_type", "xdr") or "xdr"

        if not name:
            raise serializers.ValidationError("Agent name is required")
        if not filename:
            raise serializers.ValidationError("Filename is required")
        if not isinstance(file_size, int) or file_size <= 0:
            raise serializers.ValidationError("Valid file size is required")
        if agent_type not in AGENT_TYPE_CHOICES:
            choices = ", ".join(AGENT_TYPE_CHOICES)
            raise serializers.ValidationError(f"Invalid agent type. Must be one of: {choices}")

        attrs["agent_type"] = agent_type
        return attrs


class UploadCompleteSerializer(serializers.Serializer):
    """Validate agent-upload completion requests."""

    upload_token = serializers.CharField(allow_blank=True, required=False, default="")


class UploadCancelSerializer(serializers.Serializer):
    """Validate agent-upload cancel requests."""

    upload_token = serializers.CharField(allow_blank=True, required=False, default="")


class GuacamoleInstanceSerializer(serializers.Serializer):
    """Validate range Guacamole URL bootstrap requests."""

    instance_uuid = serializers.CharField(allow_blank=False, trim_whitespace=True)


class NGFWCreateSerializer(serializers.Serializer):
    """Validate NGFW creation requests."""

    name = serializers.CharField(allow_blank=True, required=False, default="", trim_whitespace=True)
    deployment_profile_id = serializers.IntegerField(required=False, allow_null=True)
    registration_method = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=True)
    scm_credential_id = serializers.IntegerField(required=False, allow_null=True)
    otp_value = serializers.CharField(required=False, allow_blank=True, allow_null=True, trim_whitespace=True)
    otp_folder = serializers.CharField(required=False, allow_blank=True, allow_null=True, trim_whitespace=True)


class NGFWDestroySerializer(serializers.Serializer):
    """Validate NGFW destroy requests."""

    confirm_name = serializers.CharField(allow_blank=True, required=False, default="", trim_whitespace=True)


class CredentialCreateSerializer(serializers.Serializer):
    """Validate credential creation requests before schema-specific validation."""

    credential_type = serializers.CharField(trim_whitespace=True)
    name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    expires_at = serializers.CharField(required=False, allow_blank=True, allow_null=True, trim_whitespace=True)
    scm_folder_name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    scm_pin_id = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    scm_pin_value = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    sls_region = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    authcode = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)

    def validate_credential_type(self, value: str) -> str:
        if value not in ("scm", "deployment_profile"):
            raise serializers.ValidationError(f"Invalid credential type: {value}")
        return value


class ScriptUploadSerializer(serializers.Serializer):
    """Validate script upload requests.

    The endpoint is a two-step legacy-compatible flow: ``upload_token`` means
    completion; otherwise ``name``/``filename``/``file_size`` initiate.
    """

    upload_token = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    filename = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    file_size = serializers.JSONField(required=False)

    def validate(self, attrs: dict) -> dict:
        if attrs.get("upload_token"):
            return attrs
        name = attrs.get("name", "")
        filename = attrs.get("filename", "")
        file_size = attrs.get("file_size", 0)
        if not name:
            raise serializers.ValidationError("Script name is required")
        if not filename:
            raise serializers.ValidationError("Filename is required")
        if not isinstance(file_size, int) or file_size <= 0:
            raise serializers.ValidationError("Valid file size is required")
        return attrs
