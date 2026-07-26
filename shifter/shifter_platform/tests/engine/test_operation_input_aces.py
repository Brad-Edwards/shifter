"""ACES operation-input materialization (ADR-043 phase 5, #1837).

Drives what ``engine.launch_intents`` materializes into ``OperationInput`` for an
``aces-range`` generation: the serialized plan, the byte-free delivery bindings,
the plan-scoped image candidates, and the normalized backend ownership the
provisioner used to read straight out of Django tables.

These tests assert the projection through its parser, so a change that widens
what crosses the boundary fails here rather than at the provisioner.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from engine.launch_intents import enqueue_provisioner_launch
from engine.models import AcesContentDeliveryBinding, AcesImageMapping, Instance, OperationInput, Range, Request
from shared.aces.operation_input import candidate_key, parse_aces_operation_input
from shared.enums import ResourceStatus

pytestmark = pytest.mark.django_db

_SHA = "b" * 64


def _plan() -> dict:
    return {
        "kind": "aces_provisioning_plan",
        "contract_version": "aces-provisioning-plan-v1",
        "aces_sdl_version": "0.19.1",
        "resources": {
            "net.lan": {
                "address": "net.lan",
                "resource_type": "network",
                "payload": {"name": "lan", "spec": {"infrastructure": {"properties": {"cidr": "10.9.0.0/24"}}}},
            },
            "node.web": {
                "address": "node.web",
                "resource_type": "node",
                "payload": {
                    "name": "web",
                    "os_family": "linux",
                    "spec": {"node": {"source": "kali"}, "infrastructure": {"networks": ["net.lan"]}},
                },
            },
        },
    }


class _AcesRange:
    """An ACES range ready to be launched through the real intent path."""

    def __init__(self, *, status: str = ResourceStatus.PENDING.value, range_backend: str | None = "gce"):
        self.request_id = uuid4()
        self.user = get_user_model().objects.create_user(username=f"{self.request_id}@example.com")
        self.request = Request.objects.create(request_id=self.request_id, request_type="range", user=self.user)
        self.range = Range.objects.create(
            request=self.request,
            user=self.user,
            status=status,
            range_config=_plan(),
            range_backend=range_backend,
            instantiation_purpose="training",
        )

    def bind_content(self, address: str = "content.c") -> AcesContentDeliveryBinding:
        return AcesContentDeliveryBinding.objects.create(
            range=self.range,
            content_address=address,
            sha256=_SHA,
            storage_key=f"aces/content-delivery/bb/{_SHA}",
            byte_count=11,
            binding_version=1,
        )

    def launch(self, operation: str = "provision") -> OperationInput:
        enqueue_provisioner_launch(["aces-range", operation, "--request-id", str(self.request_id)])
        return OperationInput.objects.get(request_id=self.request_id, operation=operation)

    def payload(self, operation: str = "provision"):
        return parse_aces_operation_input(self.launch(operation).envelope["payload"])


def _mapping(source_name: str, *, version: str = "", enabled: bool = True, provider: str = "gce") -> AcesImageMapping:
    return AcesImageMapping.objects.create(
        provider=provider,
        source_name=source_name,
        source_version=version,
        image_ref=f"projects/p/global/images/{source_name}",
        machine_type="e2-medium",
        disk_size_gb=40,
        disk_type="pd-balanced",
        enabled=enabled,
    )


class TestPlanAndIdentity:
    def test_the_serialized_plan_crosses_verbatim(self):
        fx = _AcesRange()
        assert fx.payload().plan == _plan()

    def test_the_legacy_naming_key_is_the_range_id(self):
        fx = _AcesRange()
        assert fx.payload().legacy_range_id == fx.range.id

    def test_the_input_is_keyed_by_the_canonical_generation(self):
        fx = _AcesRange()
        row = fx.launch()
        fx.range.refresh_from_db()
        assert row.operation_id == fx.range.provisioner_operation_id

    def test_destroy_materializes_its_own_generation(self):
        fx = _AcesRange(status=ResourceStatus.DESTROYING.value)
        row = fx.launch("destroy")
        assert row.operation == "destroy"
        assert parse_aces_operation_input(row.envelope["payload"]).plan == _plan()


class TestDeliveryBindings:
    def test_bindings_cross_as_byte_free_transport(self):
        fx = _AcesRange()
        fx.bind_content()
        bindings = fx.payload().delivery_bindings
        assert [b.content_address for b in bindings] == ["content.c"]
        assert bindings[0].sha256 == _SHA

    def test_no_bindings_is_an_empty_list_not_an_error(self):
        assert _AcesRange().payload().delivery_bindings == ()

    def test_another_ranges_bindings_do_not_leak(self):
        fx = _AcesRange()
        other = _AcesRange()
        other.bind_content("content.other")
        fx.bind_content("content.mine")
        assert [b.content_address for b in fx.payload().delivery_bindings] == ["content.mine"]


class TestImageCandidates:
    def test_candidates_are_projected_for_plan_referenced_sources(self):
        fx = _AcesRange()
        _mapping("kali", version="2024.1")
        candidates = fx.payload().image_candidates_for("gce", "kali")
        assert [row["image_ref"] for row in candidates] == ["projects/p/global/images/kali"]

    def test_sources_the_plan_never_names_are_not_projected(self):
        # The registry is tenant-wide; only what this plan can ask for crosses.
        fx = _AcesRange()
        _mapping("kali")
        _mapping("windows-server-2022")
        payload = fx.payload()
        assert payload.image_candidates_for("gce", "kali")
        assert payload.image_candidates_for("gce", "windows-server-2022") == []

    def test_disabled_mappings_are_not_projected(self):
        # A retired mapping must make realization fail loud, exactly as the
        # direct SELECT ... WHERE enabled = TRUE did.
        fx = _AcesRange()
        _mapping("kali", enabled=False)
        assert fx.payload().image_candidates_for("gce", "kali") == []

    def test_registry_management_metadata_does_not_cross(self):
        fx = _AcesRange()
        _mapping("kali")
        candidate = fx.payload().image_candidates_for("gce", "kali")[0]
        assert set(candidate) == {"source_version", "image_ref", "machine_type", "disk_size_gb", "disk_type"}

    def test_candidate_keys_are_provider_scoped(self):
        fx = _AcesRange()
        _mapping("kali", provider="aws")
        payload = fx.payload()
        assert payload.image_candidates_for("gce", "kali") == []
        assert payload.image_candidates_for("aws", "kali")
        assert candidate_key("aws", "kali") == "aws:kali"


class TestBackendOwnership:
    def test_a_persisted_binding_crosses_normalized(self):
        assert _AcesRange(range_backend="gce").payload().range_backend == "gce"

    def test_an_unbound_legacy_range_resolves_from_instance_evidence(self):
        # Pre-#1666 ranges carry no binding. The Engine owns engine_instance, so
        # it resolves the backend from durable asset evidence and ships only the
        # normalized outcome.
        fx = _AcesRange(status=ResourceStatus.DESTROYING.value, range_backend=None)
        Instance.objects.create(request=fx.request, role=Instance.Role.VICTIM, state={"asset_type": "gce_vm"})
        assert fx.payload("destroy").range_backend == "gce"

    def test_ambiguous_evidence_never_becomes_a_guessed_backend(self):
        fx = _AcesRange(status=ResourceStatus.DESTROYING.value, range_backend=None)
        Instance.objects.create(request=fx.request, role=Instance.Role.VICTIM, state={"asset_type": "gce_vm"})
        Instance.objects.create(request=fx.request, role=Instance.Role.VICTIM, state={"asset_type": "scenario_pod"})
        assert fx.payload("destroy").range_backend is None

    def test_evidence_free_range_carries_no_backend(self):
        fx = _AcesRange(status=ResourceStatus.DESTROYING.value, range_backend=None)
        assert fx.payload("destroy").range_backend is None

    def test_raw_instance_state_never_crosses_the_boundary(self):
        fx = _AcesRange(status=ResourceStatus.DESTROYING.value, range_backend=None)
        Instance.objects.create(
            request=fx.request,
            role=Instance.Role.VICTIM,
            state={"asset_type": "gce_vm", "internal_ip": "10.0.0.4", "gce_instance_name": "secret-name"},
        )
        serialized = str(fx.launch("destroy").envelope)
        assert "internal_ip" not in serialized
        assert "secret-name" not in serialized
