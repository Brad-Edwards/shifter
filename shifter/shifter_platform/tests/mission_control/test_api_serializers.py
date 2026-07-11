"""Drift-guard tests for the Mission Control response-only presentation serializers (#1370).

``RangePresentationSerializer`` / ``InstancePresentationSerializer`` exist only
to give drf-spectacular a typed response schema; they mirror the pydantic
``RangeContext`` / ``InstanceContext`` projections by hand. These tests pin the
field sets against the real pydantic models so a future field added to (or
removed from) either projection fails loudly here instead of silently drifting
out of the generated OpenAPI schema / SPA TypeScript types.
"""

from __future__ import annotations

from mission_control.api.serializers import (
    InstancePresentationSerializer,
    RangePresentationSerializer,
)
from shared.schemas import InstanceContext, RangeContext


class TestRangePresentationSerializerDriftGuard:
    def test_field_set_matches_range_context_pydantic_model(self):
        expected = set(RangeContext.model_fields) | set(RangeContext.model_computed_fields)
        actual = set(RangePresentationSerializer().get_fields())
        assert actual == expected


class TestInstancePresentationSerializerDriftGuard:
    def test_field_set_matches_instance_context_pydantic_model(self):
        expected = set(InstanceContext.model_fields) | set(InstanceContext.model_computed_fields)
        actual = set(InstancePresentationSerializer().get_fields())
        assert actual == expected
