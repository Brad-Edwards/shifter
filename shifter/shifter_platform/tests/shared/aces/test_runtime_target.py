"""Tests for the Shifter ACES RuntimeTarget provisioner adapter (#1262).

``shared.aces.runtime_target`` is a translation boundary: it validates an ACES
``ProvisioningPlan`` against Shifter's ``provisioning-only`` capability
envelope (declared in :mod:`shared.aces.manifest`), translates a supported
plan into a :class:`~shared.aces.runtime_target.ShifterProvisioningIntent`,
and drives an injected :class:`~shared.aces.runtime_target.ShifterRangeRealizationPort`
to realize it. It must never dispatch a live range itself -- these tests use
an in-memory fake port and assert it is invoked for supported plans and never
invoked for unsupported ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from aces_contracts.diagnostics import Severity
from aces_contracts.planning import ChangeAction, PlannedResource, ProvisioningPlan, ProvisionOp, RuntimeDomain
from aces_contracts.runtime_state import ApplyResult, RuntimeSnapshot
from aces_runtime.registry import BackendRegistry, RuntimeTarget

from shared.aces.contracts import SHIFTER_BACKEND_NAME
from shared.aces.runtime_target import (
    NETWORK_RESOURCE_TYPE,
    NODE_RESOURCE_TYPE,
    ShifterProvisioner,
    ShifterProvisioningIntent,
    ShifterRealizationResult,
    create_shifter_backend_components,
    create_shifter_backend_target,
    register_shifter_backend,
)

FAKE_RANGE_UUID = "11111111-1111-1111-1111-111111111111"


@dataclass
class FakeRealizationPort:
    """In-memory fake port: records every intent it is asked to realize."""

    calls: list[ShifterProvisioningIntent] = field(default_factory=list)

    def realize(self, intent: ShifterProvisioningIntent) -> ShifterRealizationResult:
        self.calls.append(intent)
        return ShifterRealizationResult(range_uuid=FAKE_RANGE_UUID, status="translated")


@pytest.fixture
def fake_port() -> FakeRealizationPort:
    return FakeRealizationPort()


@pytest.fixture
def provisioner(fake_port: FakeRealizationPort) -> ShifterProvisioner:
    return ShifterProvisioner(port=fake_port)


def _node(
    address: str,
    *,
    os_family: str = "linux",
    scenario_ref: str | None = "basic",
    domain: RuntimeDomain = RuntimeDomain.PROVISIONING,
    resource_type: str = NODE_RESOURCE_TYPE,
    extra_payload: dict[str, object] | None = None,
) -> PlannedResource:
    payload: dict[str, object] = {"os_family": os_family}
    if scenario_ref is not None:
        payload["scenario_ref"] = scenario_ref
    if extra_payload:
        payload.update(extra_payload)
    return PlannedResource(address=address, domain=domain, resource_type=resource_type, payload=payload)


def _network(
    address: str,
    *,
    scenario_ref: str | None = "basic",
    domain: RuntimeDomain = RuntimeDomain.PROVISIONING,
    resource_type: str = NETWORK_RESOURCE_TYPE,
    extra_payload: dict[str, object] | None = None,
) -> PlannedResource:
    payload: dict[str, object] = {}
    if scenario_ref is not None:
        payload["scenario_ref"] = scenario_ref
    if extra_payload:
        payload.update(extra_payload)
    return PlannedResource(address=address, domain=domain, resource_type=resource_type, payload=payload)


def _plan(resources: list[PlannedResource]) -> ProvisioningPlan:
    resource_map = {resource.address: resource for resource in resources}
    operations = [
        ProvisionOp(
            action=ChangeAction.CREATE,
            address=resource.address,
            resource_type=resource.resource_type,
            payload=resource.payload,
        )
        for resource in resources
    ]
    return ProvisioningPlan(resources=resource_map, operations=operations)


class TestValidPlanTranslation:
    """A supported plan translates to the right intent and drives the port."""

    def test_validate_returns_no_diagnostics(self, provisioner: ShifterProvisioner) -> None:
        plan = _plan(
            [
                _node("plan.node.attacker", os_family="linux"),
                _node("plan.node.victim", os_family="windows"),
                _network("plan.network.core"),
            ]
        )

        diagnostics = provisioner.validate(plan)

        assert diagnostics == []

    def test_apply_calls_port_with_correct_intent(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan(
            [
                _node("plan.node.attacker", os_family="linux"),
                _node("plan.node.victim", os_family="windows"),
                _network("plan.network.core"),
            ]
        )

        result = provisioner.apply(plan, RuntimeSnapshot())

        assert isinstance(result, ApplyResult)
        assert result.success is True
        assert len(fake_port.calls) == 1
        intent = fake_port.calls[0]
        assert intent.scenario_ref == "basic"
        assert intent.node_counts_by_os == {"linux": 1, "windows": 1}
        assert intent.network_addresses == ("plan.network.core",)

    def test_apply_returns_success_snapshot_with_ids_and_status_only(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.node.attacker", os_family="linux")])

        result = provisioner.apply(plan, RuntimeSnapshot())

        assert result.success is True
        assert result.changed_addresses == ["plan.node.attacker"]
        entry = result.snapshot.get("plan.node.attacker")
        assert entry is not None
        assert entry.domain == RuntimeDomain.PROVISIONING
        assert entry.status == "translated"
        assert entry.payload == {"range_uuid": FAKE_RANGE_UUID, "status": "translated"}
        # No raw spec, no secrets -- only the realization result's IDs/status.
        assert "spec" not in entry.payload
        assert "scenario_ref" not in entry.payload

    def test_apply_ignores_operations_for_unsupported_resource_types(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        # A validatable plan (only supported resources) may still carry an
        # operation for an unsupported type; apply must skip it rather than
        # write a snapshot entry for something Shifter cannot realize.
        node = _node("plan.node.attacker", os_family="linux")
        plan = ProvisioningPlan(
            resources={node.address: node},
            operations=[
                ProvisionOp(
                    action=ChangeAction.CREATE,
                    address=node.address,
                    resource_type=NODE_RESOURCE_TYPE,
                    payload=node.payload,
                ),
                ProvisionOp(
                    action=ChangeAction.CREATE,
                    address="plan.mystery.op",
                    resource_type="mystery-resource",
                    payload={},
                ),
            ],
        )

        result = provisioner.apply(plan, RuntimeSnapshot())

        assert result.success is True
        assert result.changed_addresses == ["plan.node.attacker"]
        assert result.snapshot.get("plan.mystery.op") is None
        assert len(fake_port.calls) == 1


class TestUnsupportedClaimsAreRejected:
    """Each unsupported-capability class fails closed and never reaches the port."""

    def test_unsupported_resource_type_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.mystery.one", resource_type="mystery-resource")])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_unsupported_os_family_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.node.mac", os_family="macos")])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_non_provisioning_domain_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.node.orch", domain=RuntimeDomain.ORCHESTRATION)])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    @pytest.mark.parametrize("resource_type", ["account", "acl", "content-placement", "account-placement"])
    def test_placement_account_acl_resources_are_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort, resource_type: str
    ) -> None:
        plan = _plan([_node(f"plan.{resource_type}.one", resource_type=resource_type)])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_author_supplied_provider_detail_keys_are_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan(
            [
                _node(
                    "plan.node.attacker",
                    extra_payload={"image_id": "ami-0123456789", "terraform_vars": {"instance_type": "t3.large"}},
                )
            ]
        )

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    @pytest.mark.parametrize(
        "extra_key",
        ["cidr", "ssm_document", "ssh_key", "vpc_id"],
    )
    def test_more_provider_detail_keys_are_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort, extra_key: str
    ) -> None:
        plan = _plan([_network("plan.network.core", extra_payload={extra_key: "value"})])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_runtime_command_execution_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.command.one", resource_type="command")])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_raw_snapshot_history_requests_are_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.snapshot.one", resource_type="snapshot-request")])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_missing_scenario_ref_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan([_node("plan.node.attacker", scenario_ref=None)])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_conflicting_scenario_ref_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        plan = _plan(
            [
                _node("plan.node.attacker", scenario_ref="basic"),
                _node("plan.node.victim", scenario_ref="ad_attack_lab"),
            ]
        )

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_non_mapping_payload_is_rejected(
        self, provisioner: ShifterProvisioner, fake_port: FakeRealizationPort
    ) -> None:
        resource = PlannedResource(
            address="plan.node.bad-payload",
            domain=RuntimeDomain.PROVISIONING,
            resource_type=NODE_RESOURCE_TYPE,
            payload=["not", "a", "mapping"],  # type: ignore[arg-type]
        )
        plan = _plan([resource])

        diagnostics = provisioner.validate(plan)
        result = provisioner.apply(plan, RuntimeSnapshot())

        assert any(d.is_error for d in diagnostics)
        assert result.success is False
        assert fake_port.calls == []

    def test_port_failure_surfaces_as_diagnostic_not_exception(self, provisioner_with_raising_port) -> None:
        provisioner, port = provisioner_with_raising_port
        plan = _plan([_node("plan.node.attacker")])

        result = provisioner.apply(plan, RuntimeSnapshot())

        assert result.success is False
        assert any(d.is_error for d in result.diagnostics)
        assert port.calls == 1


@dataclass
class _RaisingPort:
    calls: int = 0

    def realize(self, intent: ShifterProvisioningIntent) -> ShifterRealizationResult:
        self.calls += 1
        raise RuntimeError("boom: realization backend unavailable")


@pytest.fixture
def provisioner_with_raising_port() -> tuple[ShifterProvisioner, _RaisingPort]:
    port = _RaisingPort()
    return ShifterProvisioner(port=port), port


class TestRuntimeTargetConstruction:
    """The constructed RuntimeTarget is provisioning-only (RUN-314 shape)."""

    def test_create_shifter_backend_target_is_provisioning_only(self, fake_port: FakeRealizationPort) -> None:
        target = create_shifter_backend_target(port=fake_port)

        assert isinstance(target, RuntimeTarget)
        assert target.name == SHIFTER_BACKEND_NAME
        assert isinstance(target.provisioner, ShifterProvisioner)
        assert target.orchestrator is None
        assert target.evaluator is None
        assert target.participant_runtime is None
        assert target.manifest.has_orchestrator is False
        assert target.manifest.has_evaluator is False
        assert target.manifest.has_participant_runtime is False

    def test_create_shifter_backend_components_matches_manifest_shape(self, fake_port: FakeRealizationPort) -> None:
        from shared.aces.manifest import create_shifter_backend_manifest

        manifest = create_shifter_backend_manifest()

        components = create_shifter_backend_components(manifest=manifest, port=fake_port)

        assert isinstance(components.provisioner, ShifterProvisioner)
        assert components.orchestrator is None
        assert components.evaluator is None
        assert components.participant_runtime is None

    def test_register_and_create_via_registry(self, fake_port: FakeRealizationPort) -> None:
        registry = BackendRegistry()

        register_shifter_backend(registry)
        target = registry.create(SHIFTER_BACKEND_NAME, port=fake_port)

        assert isinstance(target, RuntimeTarget)
        assert target.name == SHIFTER_BACKEND_NAME
        assert registry.is_registered(SHIFTER_BACKEND_NAME)

    def test_register_shifter_backend_manifest_factory_ignores_extra_config(self) -> None:
        registry = BackendRegistry()

        register_shifter_backend(registry)
        manifest = registry.manifest(SHIFTER_BACKEND_NAME, port=object(), some_other_config=True)

        assert manifest.name == SHIFTER_BACKEND_NAME


def test_diagnostics_never_carry_severity_below_error_for_hard_failures(
    provisioner: ShifterProvisioner,
) -> None:
    """Sanity check: every rejection diagnostic in this module is ERROR, not a warning."""
    plan = _plan([_node("plan.node.mac", os_family="macos")])

    diagnostics = provisioner.validate(plan)

    assert diagnostics
    assert all(d.severity == Severity.ERROR for d in diagnostics)
