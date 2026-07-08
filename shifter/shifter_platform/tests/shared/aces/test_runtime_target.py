"""Battery for the ACES-native RuntimeTarget provisioning backend (ADR-031).

Covers the interpret step (compiled ProvisioningPlan -> ProvisioningSpec) on both
hand-built plans (precise golden field assertions) and a plan compiled by the real
aces-sdl processor; the capability-envelope fail-closed negatives; apply/dispatch;
diagnostics sanitization (ADR-031-R4); and the registration/target shape. The
live conformance probe (run_target_conformance) lives in
``test_backend_conformance_gate.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from aces_backend_protocols.capabilities import ProvisionerCapabilities
from aces_contracts.planning import PlannedResource, ProvisioningPlan, RuntimeDomain
from aces_contracts.runtime_state import RuntimeSnapshot
from aces_runtime.manager import RuntimeManager
from aces_runtime.registry import BackendRegistry
from aces_sdl.parser import parse_sdl

from shared.aces.contracts import SHIFTER_BACKEND_NAME
from shared.aces.dispatch_port import ShifterDispatchResult
from shared.aces.manifest import create_shifter_backend_manifest
from shared.aces.runtime_target import (
    NETWORK_RESOURCE_TYPE,
    NODE_RESOURCE_TYPE,
    ShifterProvisioner,
    create_shifter_backend_components,
    create_shifter_backend_target,
    interpret_provisioning_plan,
    register_shifter_backend,
)

REQUEST_ID = "11111111-1111-1111-1111-111111111111"

_FORBIDDEN_DIAGNOSTIC_SUBSTRINGS = (
    "terraform",
    "ssm",
    "ami-",
    "cidr",
    "subnet",
    "secret",
    "password",
    "credential",
    "token",
    "-----begin",
)


@dataclass
class FakeDispatchPort:
    """Recording dispatch port: no DB/cloud, returns an accepted result."""

    request_id: str = REQUEST_ID
    accepted: bool = True
    raises: Exception | None = None
    specs: list = field(default_factory=list)

    def realize(self, spec) -> ShifterDispatchResult:
        if self.raises is not None:
            raise self.raises
        self.specs.append(spec)
        return ShifterDispatchResult(
            request_id=self.request_id, accepted=self.accepted, status="accepted", range_id="rng-1"
        )


def _node(
    address: str,
    name: str,
    *,
    os_family: str = "linux",
    node_type: str = "vm",
    count: int = 1,
    links: tuple[str, ...] = (),
    acls: list | None = None,
    ram_bytes: int | None = 2 * 1024 * 1024 * 1024,
    cpu: int | None = 2,
    source: object = None,
    services: list | None = None,
) -> PlannedResource:
    node_spec: dict = {"type": node_type, "os": os_family}
    resources: dict = {}
    if ram_bytes is not None:
        resources["ram"] = ram_bytes
    if cpu is not None:
        resources["cpu"] = cpu
    if resources:
        node_spec["resources"] = resources
    if source is not None:
        node_spec["source"] = source
    if services is not None:
        node_spec["services"] = services
    infra: dict = {"links": list(links), "count": count}
    if acls is not None:
        infra["acls"] = acls
    return PlannedResource(
        address=address,
        domain=RuntimeDomain.PROVISIONING,
        resource_type=NODE_RESOURCE_TYPE,
        payload={
            "name": name,
            "node_name": name,
            "node_type": node_type,
            "os_family": os_family,
            "count": count,
            "spec": {"node": node_spec, "infrastructure": infra},
        },
    )


def _network(address: str, name: str, *, cidr: str = "10.0.0.0/24", internal: bool = False) -> PlannedResource:
    return PlannedResource(
        address=address,
        domain=RuntimeDomain.PROVISIONING,
        resource_type=NETWORK_RESOURCE_TYPE,
        payload={
            "name": name,
            "node_name": name,
            "spec": {
                "node": {"type": "switch"},
                "infrastructure": {"properties": {"cidr": cidr, "gateway": "10.0.0.1", "internal": internal}},
            },
        },
    )


def _plan(*resources: PlannedResource) -> ProvisioningPlan:
    return ProvisioningPlan(resources={r.address: r for r in resources}, operations=[])


def _interpret(plan: ProvisioningPlan, **kwargs):
    return interpret_provisioning_plan(plan, request_id=REQUEST_ID, **kwargs)


# --- interpret: faithful topology (golden field assertions) --------------------


def test_interpret_carries_full_node_and_network_topology() -> None:
    plan = _plan(
        _node(
            "provision.node.web",
            "web",
            count=3,
            links=("lan",),
            source={"name": "ubuntu-22.04", "version": "1.2"},
            services=[{"name": "ssh", "port": 22, "protocol": "tcp"}],
        ),
        _network("provision.network.lan", "lan", cidr="10.9.0.0/24", internal=True),
    )
    spec, diagnostics = _interpret(plan)
    assert [d for d in diagnostics if d.is_error] == []
    assert spec is not None
    assert spec.request_id == REQUEST_ID
    node = spec.nodes[0]
    assert node.os_family == "linux"
    assert node.count == 3
    assert node.resources.ram_mib == 2048  # 2 GiB -> MiB
    assert node.resources.vcpus == 2
    assert node.image is not None and node.image.name == "ubuntu-22.04" and node.image.version == "1.2"
    assert [s.port for s in node.services] == [22]
    assert node.network_addresses == ("provision.network.lan",)
    network = spec.networks[0]
    assert network.cidr == "10.9.0.0/24" and network.gateway == "10.0.0.1" and network.internal is True


def test_interpret_image_source_as_bare_string() -> None:
    spec, _ = _interpret(_plan(_node("provision.node.a", "a", source="win2022-template")))
    assert spec is not None and spec.nodes[0].image is not None
    assert spec.nodes[0].image.name == "win2022-template" and spec.nodes[0].image.version is None


def test_interpret_absent_resources_and_image_are_none() -> None:
    spec, _ = _interpret(_plan(_node("provision.node.a", "a", ram_bytes=None, cpu=None, source=None)))
    assert spec is not None
    node = spec.nodes[0]
    assert node.resources.ram_mib is None and node.resources.vcpus is None and node.image is None


def test_interpret_small_ram_value_treated_as_mib() -> None:
    spec, _ = _interpret(_plan(_node("provision.node.a", "a", ram_bytes=512)))
    assert spec is not None and spec.nodes[0].resources.ram_mib == 512


# --- interpret: real compiled plan --------------------------------------------


def test_interpret_consumes_real_compiled_plan() -> None:
    scenario = parse_sdl(
        'name: rt-battery-probe\nversion: "1.0.0"\nnodes:\n  web1:\n    type: vm\n    os: linux\n'
        "  dc1:\n    type: vm\n    os: windows\n"
    )
    target = create_shifter_backend_target(port=FakeDispatchPort())
    execution_plan = RuntimeManager(target).plan(scenario)
    spec, diagnostics = _interpret(execution_plan.provisioning)
    assert [d for d in diagnostics if d.is_error] == []
    assert spec is not None
    assert sorted(n.os_family for n in spec.nodes) == ["linux", "windows"]


# --- capability envelope: fail closed -----------------------------------------


@pytest.mark.parametrize(
    ("plan_factory", "expected_code"),
    [
        (lambda: _plan(_node("provision.node.a", "a", os_family="macos")), "shifter-provisioner.unsupported-os-family"),
        (
            lambda: _plan(_node("provision.node.a", "a", node_type="container")),
            "shifter-provisioner.unsupported-node-type",
        ),
        (
            lambda: _plan(
                _node("provision.node.a", "a", links=("lan",), acls=[{"action": "allow", "direction": "in"}]),
                _network("provision.network.lan", "lan"),
            ),
            "shifter-provisioner.acls-unsupported",
        ),
        (lambda: _plan(_node("provision.node.a", "a", links=("ghost",))), "shifter-provisioner.unknown-network"),
        (
            lambda: _plan(
                PlannedResource(
                    address="provision.account-placement.x",
                    domain=RuntimeDomain.PROVISIONING,
                    resource_type="account-placement",
                    payload={"name": "x"},
                )
            ),
            "shifter-provisioner.unsupported-resource-type",
        ),
    ],
)
def test_out_of_envelope_terms_fail_closed(plan_factory, expected_code: str) -> None:
    spec, diagnostics = _interpret(plan_factory())
    assert spec is None
    assert any(d.is_error and d.code == expected_code for d in diagnostics)


def test_node_budget_enforced() -> None:
    capped = ProvisionerCapabilities(
        name="capped",
        supported_node_types=frozenset({"vm"}),
        supported_os_families=frozenset({"linux", "windows"}),
        max_total_nodes=1,
    )
    spec, diagnostics = _interpret(_plan(_node("provision.node.a", "a", count=5)), capabilities=capped)
    assert spec is None
    assert any(d.code == "shifter-provisioner.node-budget-exceeded" for d in diagnostics)


def test_non_mapping_payload_rejected() -> None:
    bad = PlannedResource(
        address="provision.node.a", domain=RuntimeDomain.PROVISIONING, resource_type=NODE_RESOURCE_TYPE, payload=[]
    )
    spec, diagnostics = _interpret(_plan(bad))
    assert spec is None
    assert any(d.code == "shifter-provisioner.invalid-payload" for d in diagnostics)


def test_all_diagnostics_are_bounded_and_sanitized() -> None:
    plans = [
        _plan(_node("provision.node.a", "a", os_family="freebsd")),
        _plan(_node("provision.node.a", "a", node_type="container")),
        _plan(_node("provision.node.a", "a", links=("ghost",))),
    ]
    for plan in plans:
        _, diagnostics = _interpret(plan)
        for diagnostic in diagnostics:
            assert "\n" not in diagnostic.message
            assert len(diagnostic.message) <= 480
            lowered = diagnostic.message.lower()
            assert not any(marker in lowered for marker in _FORBIDDEN_DIAGNOSTIC_SUBSTRINGS)


# --- apply / dispatch ----------------------------------------------------------


def test_apply_dispatches_and_reports_provisioning_snapshot() -> None:
    port = FakeDispatchPort()
    plan = _plan(_node("provision.node.web", "web", links=("lan",)), _network("provision.network.lan", "lan"))
    result = ShifterProvisioner(port).apply(plan, RuntimeSnapshot())
    assert result.success is True
    assert set(result.changed_addresses) == {"provision.node.web", "provision.network.lan"}
    provisioning_entries = [e for e in result.snapshot.entries.values() if e.domain == RuntimeDomain.PROVISIONING]
    assert len(provisioning_entries) == 2
    assert all(entry.payload["request_id"] == REQUEST_ID for entry in provisioning_entries)
    assert len(port.specs) == 1


def test_apply_does_not_dispatch_on_invalid_plan() -> None:
    port = FakeDispatchPort()
    result = ShifterProvisioner(port).apply(_plan(_node("provision.node.a", "a", os_family="macos")), RuntimeSnapshot())
    assert result.success is False
    assert port.specs == []


def test_apply_wraps_dispatch_failure_as_diagnostic() -> None:
    port = FakeDispatchPort(raises=RuntimeError("boom"))
    plan = _plan(_node("provision.node.web", "web"))
    result = ShifterProvisioner(port).apply(plan, RuntimeSnapshot())
    assert result.success is False
    assert any(d.code == "shifter-provisioner.dispatch-failed" for d in result.diagnostics)


def test_apply_and_validate_reject_non_plan() -> None:
    provisioner = ShifterProvisioner(FakeDispatchPort())
    assert provisioner.validate("nope")[0].code == "shifter-provisioner.invalid-plan"
    result = provisioner.apply("nope", RuntimeSnapshot())
    assert result.success is False


def test_validate_clean_plan_has_no_errors() -> None:
    plan = _plan(_node("provision.node.web", "web", links=("lan",)), _network("provision.network.lan", "lan"))
    assert [d for d in ShifterProvisioner(FakeDispatchPort()).validate(plan) if d.is_error] == []


# --- registration / target shape ----------------------------------------------


def test_create_target_is_provisioning_only() -> None:
    target = create_shifter_backend_target(port=FakeDispatchPort())
    assert target.manifest.has_orchestrator is False
    assert target.manifest.has_evaluator is False
    assert target.manifest.has_participant_runtime is False
    assert isinstance(target.provisioner, ShifterProvisioner)


def test_components_match_manifest_shape() -> None:
    manifest = create_shifter_backend_manifest()
    components = create_shifter_backend_components(manifest=manifest, port=FakeDispatchPort())
    assert isinstance(components.provisioner, ShifterProvisioner)
    assert components.orchestrator is None
    assert components.evaluator is None


def test_register_shifter_backend() -> None:
    registry = BackendRegistry()
    register_shifter_backend(registry)
    assert registry.is_registered(SHIFTER_BACKEND_NAME)
