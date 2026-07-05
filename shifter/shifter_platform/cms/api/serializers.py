"""Serializers for the CMS DRF API."""

from __future__ import annotations

from rest_framework import serializers


class YAMLContentSerializer(serializers.Serializer):
    """Validate a YAML-content request body."""

    yaml_content = serializers.CharField(allow_blank=True, trim_whitespace=False)
