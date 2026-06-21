"""Wire-contract canary tests for cross-runtime constants.

These values are persisted and exchanged across the provisioner/process
boundary. A rename or value change must fail here, not ship silently.
"""

from __future__ import annotations

import hashlib

import pytest

from cyberscript.channels import groups
from cyberscript.enums import ResourceStatus
from cyberscript import wire_constants as event_types


class TestResourceStatusCanary:
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ResourceStatus.PENDING, "pending"),
            (ResourceStatus.PROVISIONING, "provisioning"),
            (ResourceStatus.READY, "ready"),
            (ResourceStatus.PAUSING, "pausing"),
            (ResourceStatus.PAUSED, "paused"),
            (ResourceStatus.RESUMING, "resuming"),
            (ResourceStatus.DESTROYING, "destroying"),
            (ResourceStatus.DESTROYED, "destroyed"),
            (ResourceStatus.FAILED, "failed"),
        ],
    )
    def test_resource_status_values_are_frozen(self, member: ResourceStatus, value: str) -> None:
        assert member.value == value


class TestEventTypeCanary:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("EVENT_TYPE_STATUS_UPDATED", "range.status.updated"),
            ("EVENT_TYPE_PROVISIONED", "range.provisioned"),
            ("EVENT_TYPE_DESTROYED", "range.destroyed"),
            ("EVENT_TYPE_CANCELLED", "range.cancelled"),
            ("EVENT_TYPE_NGFW", "ngfw.event"),
        ],
    )
    def test_event_type_constants_are_frozen(self, name: str, value: str) -> None:
        assert getattr(event_types, name) == value


class TestChannelGroupCanary:
    def test_range_event_group_format(self) -> None:
        assert groups.range_event_group("abc-123") == "range_status_abc-123"
        assert groups.range_event_group(42) == "range_status_42"

    def test_user_event_group_format(self) -> None:
        assert groups.user_event_group(42) == "user_42"

    def test_ngfw_event_group_format(self) -> None:
        assert groups.ngfw_event_group("abc-123") == "ngfw_status_abc-123"

    def test_notification_topic_group_is_stable_and_bounded(self) -> None:
        topic = "experiment:100"
        expected = "notify_u42_" + hashlib.sha256(topic.encode("utf-8")).hexdigest()[:32]
        name = groups.notification_user_topic_group(42, topic)
        assert name == expected
        assert len(name) <= 100
        assert ":" not in name
