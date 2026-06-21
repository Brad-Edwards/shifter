"""Cross-package wire-contract drift guard for the provisioner publisher."""

from __future__ import annotations

from cyberscript import wire_constants as event_types
from cyberscript.enums import ResourceStatus


class TestProvisionerEventsMatchesCyberscript:
    def test_provisioner_reexports_event_types(self) -> None:
        import events as provisioner_events

        for name in event_types.__all__:
            assert getattr(provisioner_events, name) == getattr(event_types, name)

    def test_provisioner_status_aliases_match_resource_status(self) -> None:
        import events as provisioner_events

        for status in ResourceStatus:
            alias = f"STATUS_{status.name}"
            assert getattr(provisioner_events, alias) == status.value
