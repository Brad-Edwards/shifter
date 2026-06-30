"""Serializers for the CMS DRF API."""

from __future__ import annotations

from rest_framework import serializers


class YAMLContentSerializer(serializers.Serializer):
    """Validate a YAML-content request body."""

    yaml_content = serializers.CharField(allow_blank=True, trim_whitespace=False)


class ScriptUploadInitiateSerializer(serializers.Serializer):
    """Validate script-upload initiation requests."""

    name = serializers.CharField(allow_blank=False, trim_whitespace=True)
    filename = serializers.CharField(allow_blank=False, trim_whitespace=True)
    file_size = serializers.IntegerField(min_value=1)


class ScriptUploadCompleteSerializer(serializers.Serializer):
    """Validate script-upload completion requests."""

    upload_token = serializers.CharField(allow_blank=False, trim_whitespace=True)
