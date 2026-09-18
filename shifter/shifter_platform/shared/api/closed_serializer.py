"""Closed write contracts reject ignored authority or transport fields."""

from rest_framework import serializers


class ClosedSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Unknown field."]})
        return super().to_internal_value(data)
