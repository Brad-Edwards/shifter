"""Admission tests for ACES 0.23 authored identity-domain topology (#1606)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import PlannedResource, ProvisioningPlan, RuntimeDomain
from aces_contracts.runtime_state import RuntimeSnapshot, SnapshotEntry
from aces_processor.compiler import compile_runtime_model
from aces_processor.planner import plan as compile_plan
from aces_sdl.parser import parse_sdl

from shared.aces.dispatch_port import ShifterDispatchResult
from shared.aces.manifest import create_shifter_backend_manifest
from shared.aces.runtime_target import ShifterProvisioner, interpret_provisioning_plan


@dataclass
class RecordingPort:
    """Record accepted serialized plans without performing I/O."""

    plans: list[dict] = field(default_factory=list)

    def realize(self, compiled_plan: dict) -> ShifterDispatchResult:
        self.plans.append(compiled_plan)
        return ShifterDispatchResult(
            request_id="11111111-1111-1111-1111-111111111111",
            accepted=True,
            status="accepted",
            range_id="rng-1",
        )


def _compiled_domain_plan() -> ProvisioningPlan:
    scenario = parse_sdl(
        """
        name: domain-topology-probe
        nodes:
          dc: {type: vm, os: windows}
          member: {type: vm, os: windows}
        accounts:
          domain-admin: {username: Administrator, node: dc}
          web-service:
            username: svc-web
            node: member
            spn: HTTP/member.corp.example
            domain_ref: corp
        identity_domains:
          corp:
            profile: active_directory
            dns_name: corp.example
            netbios_name: CORP
            authority_account_ref: domain-admin
        relationships:
          controller:
            type: domain_controller_for
            source: dc
            target: corp
            domain_controller: {}
          member-join:
            type: joins_domain
            source: member
            target: corp
            domain_join: {controller_refs: [dc]}
        """
    )
    manifest = create_shifter_backend_manifest()
    topology_capabilities = replace(
        manifest.provisioner,
        supported_domain_profiles=frozenset({"active_directory"}),
        supported_account_features=manifest.provisioner.supported_account_features | {"spn"},
    )
    compilation_manifest = replace(
        manifest,
        capabilities=replace(manifest.capabilities, provisioner=topology_capabilities),
    )
    return compile_plan(compile_runtime_model(scenario), compilation_manifest).provisioning


def test_real_compiled_domain_topology_is_rejected_by_honest_manifest_before_dispatch() -> None:
    port = RecordingPort()

    result = ShifterProvisioner(port).apply(_compiled_domain_plan(), RuntimeSnapshot())

    assert result.success is False
    assert port.plans == []
    assert any(d.code == "provisioner.unsupported-domain-profile" for d in result.diagnostics)


def test_domain_topology_diagnostic_messages_are_replaced_without_changing_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_dns_name = "redaction-probe.example"
    sensitive_netbios_name = "REDACTION-PROBE"
    upstream_code = "provisioning.domain-topology.redaction-probe"
    upstream = Diagnostic(
        code=upstream_code,
        domain="provisioning",
        address="provision.node.dc",
        message=f"identity domain {sensitive_dns_name} ({sensitive_netbios_name}) is invalid",
        severity=Severity.ERROR,
    )
    monkeypatch.setattr(
        "shared.aces.domain_topology.domain_topology_plan_diagnostics",
        lambda *_args, **_kwargs: [upstream],
    )

    serialized, diagnostics = interpret_provisioning_plan(ProvisioningPlan(resources={}))

    assert serialized is None
    assert len(diagnostics) == 1
    assert diagnostics[0].code == upstream_code
    assert diagnostics[0].address == upstream.address
    assert diagnostics[0].message == ("authored identity-domain topology is invalid or unsupported by this backend")
    assert sensitive_dns_name not in diagnostics[0].message
    assert sensitive_netbios_name not in diagnostics[0].message


def test_malformed_domain_topology_fails_before_serialization() -> None:
    node = PlannedResource(
        address="provision.node.dc",
        domain=RuntimeDomain.PROVISIONING,
        resource_type="node",
        payload={
            "name": "dc",
            "os_family": "windows",
            "spec": {"node": {"type": "vm", "os": "windows"}},
            "domain_topology": "active_directory",
        },
    )

    serialized, diagnostics = interpret_provisioning_plan(ProvisioningPlan(resources={node.address: node}))

    assert serialized is None
    assert any(d.code == "provisioning.domain-topology.binding-invalid" for d in diagnostics)


def test_apply_uses_snapshot_for_incremental_domain_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    full_plan = _compiled_domain_plan()
    operations = {operation.address: operation for operation in full_plan.operations}
    snapshot = RuntimeSnapshot(
        entries={
            address: SnapshotEntry(
                address=operation.address,
                domain=RuntimeDomain.PROVISIONING,
                resource_type=operation.resource_type,
                payload=operation.payload,
                ordering_dependencies=operation.ordering_dependencies,
                refresh_dependencies=operation.refresh_dependencies,
            )
            for address in ("provision.node.dc", "provision.account.domain-admin")
            for operation in (operations[address],)
        }
    )
    incremental = ProvisioningPlan(operations=[operations["provision.node.member"]])
    supported = replace(
        create_shifter_backend_manifest().provisioner,
        supported_domain_profiles=frozenset({"active_directory"}),
    )
    monkeypatch.setattr("shared.aces.runtime_target.SHIFTER_PROVISIONER_CAPABILITIES", supported)
    port = RecordingPort()

    assert any(
        d.code == "provisioning.domain-topology.controller-unbound"
        for d in ShifterProvisioner(port).validate(incremental)
    )
    result = ShifterProvisioner(port).apply(incremental, snapshot)

    assert result.success is True
    assert result.diagnostics == []
    assert len(port.plans) == 1
