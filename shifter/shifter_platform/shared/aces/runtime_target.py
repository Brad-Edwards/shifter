"""Shifter's ACES RuntimeTarget provisioning backend (ADR-031).

Supersedes the #1262 ``scenario_ref`` passthrough. This backend faithfully
interprets a compiled ACES ``ProvisioningPlan`` into the neutral, locked
:class:`~shared.aces.provisioning_spec.ProvisioningSpec` (nodes with os family,
count, cpu/memory resources, image reference, services, network membership;
networks with cidr, gateway, isolation), then dispatches it through an injected
:class:`~shared.aces.dispatch_port.ShifterProvisioningDispatchPort`.

It mirrors the ``aces_backend_libvirt`` / APTL reference backend pattern:

* ``validate`` and ``apply`` funnel through one pure interpret step (no I/O);
* every plan term is checked against the declared ``ProvisionerCapabilities``
  envelope and any out-of-envelope term (unsupported node type, OS family, ACL
  when ``supports_acls`` is false, or account/placement resource) yields a
  bounded typed ERROR diagnostic;
* ``apply`` refuses to dispatch on any error, and on a valid plan returns an
  ``ApplyResult`` with non-empty ``changed_addresses`` and a PROVISIONING
  ``RuntimeSnapshot`` reflecting the accepted realization.

This module and :mod:`shared.aces.manifest` are the only modules allowed to
import the ``aces-sdl`` tooling (ADR-031-R1 / ADR-024); the realization side
consumes the ``ProvisioningSpec`` via the injected dispatch port.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from aces_backend_protocols.capabilities import BackendManifest, ProvisionerCapabilities
from aces_contracts.diagnostics import Diagnostic, Severity
from aces_contracts.planning import PlannedResource, ProvisioningPlan, RuntimeDomain
from aces_contracts.runtime_state import ApplyResult, RuntimeSnapshot, SnapshotEntry
from aces_runtime.registry import BackendRegistry, RuntimeTarget, RuntimeTargetComponents

from shared.aces.contracts import SHIFTER_BACKEND_NAME
from shared.aces.dispatch_port import ShifterProvisioningDispatchPort
from shared.aces.manifest import SHIFTER_PROVISIONER_CAPABILITIES, create_shifter_backend_manifest
from shared.aces.provisioning_spec import (
    ProvisioningSpec,
    ProvisioningSpecError,
    validate_provisioning_spec,
)
from shared.log_sanitize import safe_log_value

__all__ = [
    "NETWORK_RESOURCE_TYPE",
    "NODE_RESOURCE_TYPE",
    "SUPPORTED_RESOURCE_TYPES",
    "ShifterProvisioner",
    "create_shifter_backend_components",
    "create_shifter_backend_target",
    "interpret_provisioning_plan",
    "register_shifter_backend",
]

_DOMAIN = "provisioning"
NODE_RESOURCE_TYPE = "node"
NETWORK_RESOURCE_TYPE = "network"
SUPPORTED_RESOURCE_TYPES: frozenset[str] = frozenset({NODE_RESOURCE_TYPE, NETWORK_RESOURCE_TYPE})

# A valid placeholder request id used only when validating a plan (validate()
# only cares about diagnostics; apply() builds the real spec with the port's id).
_VALIDATE_REQUEST_ID = "00000000-0000-0000-0000-000000000000"
_MAX_DIAGNOSTIC = 480


def _diagnostic(code: str, address: str, message: str) -> Diagnostic:
    """Build a bounded, single-line ERROR provisioning diagnostic (ADR-031-R4)."""
    flat = " ".join(str(message).split())
    if len(flat) > _MAX_DIAGNOSTIC:
        flat = flat[: _MAX_DIAGNOSTIC - 1] + "…"
    return Diagnostic(code=code, domain=_DOMAIN, address=address, message=flat, severity=Severity.ERROR)


# --- payload accessors (mirror aces_backend_libvirt so interpret + envelope agree) ---


def _spec(payload: Mapping[str, object]) -> Mapping[str, object]:
    spec = payload.get("spec")
    return spec if isinstance(spec, Mapping) else {}


def _node_spec(payload: Mapping[str, object]) -> Mapping[str, object]:
    node = _spec(payload).get("node")
    return node if isinstance(node, Mapping) else {}


def _infrastructure_spec(payload: Mapping[str, object]) -> Mapping[str, object]:
    infra = _spec(payload).get("infrastructure")
    return infra if isinstance(infra, Mapping) else {}


def _resource_name(resource: PlannedResource, payload: Mapping[str, object]) -> str:
    name = payload.get("name") or payload.get("node_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return resource.address.rsplit(".", 1)[-1]


def _os_family(payload: Mapping[str, object]) -> str:
    family = payload.get("os_family")
    if isinstance(family, str) and family.strip():
        return family.strip()
    node_os = _node_spec(payload).get("os")
    return node_os.strip() if isinstance(node_os, str) and node_os.strip() else ""


def _node_type(payload: Mapping[str, object]) -> str:
    node_type = payload.get("node_type")
    if isinstance(node_type, str) and node_type.strip():
        return node_type.strip()
    nested = _node_spec(payload).get("type")
    return nested.strip() if isinstance(nested, str) and nested.strip() else ""


def _node_count(payload: Mapping[str, object]) -> int:
    raw = payload.get("count")
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int) and raw >= 1:
        return raw
    if isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError:
            return 1
        return value if value >= 1 else 1
    return 1


def _resources(payload: Mapping[str, object]) -> dict[str, int]:
    raw = _node_spec(payload).get("resources")
    resources = raw if isinstance(raw, Mapping) else {}
    out: dict[str, int] = {}
    ram = resources.get("ram")
    if isinstance(ram, int | float) and not isinstance(ram, bool) and ram > 0:
        # Planner payloads carry RAM in bytes; small hand-authored values are MiB.
        ram_mib = math.ceil(ram / (1024 * 1024)) if ram >= 1024 * 1024 else int(ram)
        out["ram_mib"] = max(1, ram_mib)
    cpu = resources.get("cpu")
    if isinstance(cpu, int | float) and not isinstance(cpu, bool) and cpu > 0:
        out["vcpus"] = max(1, int(cpu))
    return out


def _image(payload: Mapping[str, object]) -> dict[str, str] | None:
    source = _node_spec(payload).get("source")
    if isinstance(source, str) and source.strip():
        return {"name": source.strip()}
    if isinstance(source, Mapping):
        name = source.get("name")
        if isinstance(name, str) and name.strip():
            image: dict[str, str] = {"name": name.strip()}
            version = source.get("version")
            if isinstance(version, str) and version.strip():
                image["version"] = version.strip()
            return image
    return None


def _services(payload: Mapping[str, object]) -> list[dict[str, Any]]:
    raw = _node_spec(payload).get("services")
    if not isinstance(raw, list | tuple):
        return []
    services: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        port = item.get("port")
        if isinstance(name, str) and name.strip() and isinstance(port, int) and not isinstance(port, bool):
            protocol = item.get("protocol")
            services.append(
                {
                    "name": name.strip(),
                    "port": port,
                    "protocol": protocol.strip().lower() if isinstance(protocol, str) and protocol.strip() else "tcp",
                }
            )
    return services


def _network_refs(payload: Mapping[str, object]) -> tuple[str, ...]:
    infra = _infrastructure_spec(payload)
    for field_name in ("networks", "links"):
        raw = infra.get(field_name)
        if isinstance(raw, list | tuple):
            return tuple(ref for ref in raw if isinstance(ref, str) and ref.strip())
    return ()


def _network_lookup(network_resources: list[tuple[PlannedResource, Mapping[str, object]]]) -> dict[str, str]:
    """Map every handle a node might reference a network by to its canonical address."""
    lookup: dict[str, str] = {}
    for resource, payload in network_resources:
        name = _resource_name(resource, payload)
        for key in (resource.address, name, resource.address.rsplit(".", 1)[-1]):
            if key:
                lookup[key] = resource.address
    return lookup


def _network_kwargs(resource: PlannedResource, payload: Mapping[str, object]) -> dict[str, Any]:
    properties = _infrastructure_spec(payload).get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    kwargs: dict[str, Any] = {"address": resource.address, "name": _resource_name(resource, payload)}
    cidr = properties.get("cidr")
    if isinstance(cidr, str) and cidr.strip():
        kwargs["cidr"] = cidr.strip()
    gateway = properties.get("gateway")
    if isinstance(gateway, str) and gateway.strip():
        kwargs["gateway"] = gateway.strip()
    kwargs["internal"] = properties.get("internal") is True
    return kwargs


# --- capability envelope (fail closed on out-of-envelope terms) ---


def _capability_envelope_diagnostics(
    resources: list[PlannedResource], capabilities: ProvisionerCapabilities
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    total_nodes = 0
    for resource in resources:
        payload = resource.payload
        if not isinstance(payload, Mapping):
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.invalid-payload", resource.address, "resource payload must be a mapping"
                )
            )
            continue
        if resource.resource_type not in SUPPORTED_RESOURCE_TYPES:
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.unsupported-resource-type",
                    resource.address,
                    f"provisioning-only backend does not support resource type '{resource.resource_type}' "
                    f"(supported: {sorted(SUPPORTED_RESOURCE_TYPES)})",
                )
            )
            continue
        if resource.resource_type == NETWORK_RESOURCE_TYPE:
            continue
        # node
        total_nodes += _node_count(payload)
        node_type = _node_type(payload)
        if node_type and node_type not in capabilities.supported_node_types:
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.unsupported-node-type",
                    resource.address,
                    f"unsupported node type '{node_type}' (supported: {sorted(capabilities.supported_node_types)})",
                )
            )
        os_family = _os_family(payload)
        if os_family and os_family not in capabilities.supported_os_families:
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.unsupported-os-family",
                    resource.address,
                    f"unsupported os_family '{safe_log_value(os_family)}' "
                    f"(supported: {sorted(capabilities.supported_os_families)})",
                )
            )
        if not capabilities.supports_acls and _infrastructure_spec(payload).get("acls"):
            diagnostics.append(
                _diagnostic(
                    "shifter-provisioner.acls-unsupported",
                    resource.address,
                    "this provisioning-only backend does not yet realize network ACLs",
                )
            )
    if capabilities.max_total_nodes is not None and total_nodes > capabilities.max_total_nodes:
        diagnostics.append(
            _diagnostic(
                "shifter-provisioner.node-budget-exceeded",
                "plan",
                f"plan requests {total_nodes} nodes; backend allows at most {capabilities.max_total_nodes}",
            )
        )
    return diagnostics


# --- interpret ---


def interpret_provisioning_plan(
    plan: ProvisioningPlan,
    *,
    request_id: str,
    capabilities: ProvisionerCapabilities | None = None,
) -> tuple[ProvisioningSpec | None, list[Diagnostic]]:
    """Interpret a compiled ACES provisioning plan into a ProvisioningSpec.

    Pure (no I/O). Returns ``(spec, diagnostics)`` on a fully-supported plan, or
    ``(None, diagnostics)`` with at least one ERROR diagnostic when any plan term
    is outside the backend capability envelope or the resulting spec is invalid.
    """
    capabilities = capabilities or SHIFTER_PROVISIONER_CAPABILITIES
    provisioning = [
        resource
        for resource in sorted(plan.resources.values(), key=lambda item: item.address)
        if resource.domain == RuntimeDomain.PROVISIONING
    ]
    diagnostics = _capability_envelope_diagnostics(provisioning, capabilities)

    network_resources = [
        (r, r.payload)
        for r in provisioning
        if r.resource_type == NETWORK_RESOURCE_TYPE and isinstance(r.payload, Mapping)
    ]
    node_resources = [
        (r, r.payload) for r in provisioning if r.resource_type == NODE_RESOURCE_TYPE and isinstance(r.payload, Mapping)
    ]

    networks = [_network_kwargs(resource, payload) for resource, payload in network_resources]
    lookup = _network_lookup(network_resources)

    nodes: list[dict[str, Any]] = []
    for resource, payload in node_resources:
        resolved: list[str] = []
        for ref in _network_refs(payload):
            address = lookup.get(ref)
            if address is None:
                diagnostics.append(
                    _diagnostic(
                        "shifter-provisioner.unknown-network",
                        resource.address,
                        f"node references network '{ref}' not declared in this plan",
                    )
                )
                continue
            resolved.append(address)
        node_kwargs: dict[str, Any] = {
            "address": resource.address,
            "name": _resource_name(resource, payload),
            "os_family": _os_family(payload) or "unknown",
            "count": _node_count(payload),
            "network_addresses": tuple(dict.fromkeys(resolved)),
        }
        resources = _resources(payload)
        if resources:
            node_kwargs["resources"] = resources
        image = _image(payload)
        if image is not None:
            node_kwargs["image"] = image
        services = _services(payload)
        if services:
            node_kwargs["services"] = services
        nodes.append(node_kwargs)

    if any(diagnostic.is_error for diagnostic in diagnostics):
        return None, diagnostics

    try:
        spec = validate_provisioning_spec({"request_id": request_id, "nodes": nodes, "networks": networks})
    except ProvisioningSpecError as exc:
        diagnostics.append(_diagnostic("shifter-provisioner.invalid-spec", "plan", str(exc)))
        return None, diagnostics
    return spec, diagnostics


class ShifterProvisioner:
    """Provisioner protocol implementation for Shifter's provisioning-only backend."""

    def __init__(self, port: ShifterProvisioningDispatchPort) -> None:
        self._port = port

    def validate(self, plan: ProvisioningPlan) -> list[Diagnostic]:
        """Return capability-envelope + interpretation diagnostics without dispatching."""
        if not isinstance(plan, ProvisioningPlan):
            return [_diagnostic("shifter-provisioner.invalid-plan", "plan", "expected an ACES ProvisioningPlan")]
        _, diagnostics = interpret_provisioning_plan(plan, request_id=_VALIDATE_REQUEST_ID)
        return diagnostics

    def apply(self, plan: ProvisioningPlan, snapshot: RuntimeSnapshot) -> ApplyResult:
        """Interpret + validate + dispatch ``plan``; never dispatch on error."""
        if not isinstance(plan, ProvisioningPlan):
            return ApplyResult(
                success=False,
                snapshot=snapshot,
                diagnostics=[
                    _diagnostic("shifter-provisioner.invalid-plan", "plan", "expected an ACES ProvisioningPlan")
                ],
            )
        spec, diagnostics = interpret_provisioning_plan(plan, request_id=self._port.request_id)
        if spec is None:
            return ApplyResult(success=False, snapshot=snapshot, diagnostics=diagnostics)

        try:
            result = self._port.realize(spec)
        except Exception as exc:  # boundary: never leak a raw exception past apply
            failure = _diagnostic(
                "shifter-provisioner.dispatch-failed",
                "plan",
                f"provisioning dispatch failed: {safe_log_value(exc)}",
            )
            return ApplyResult(success=False, snapshot=snapshot, diagnostics=[*diagnostics, failure])

        entries = dict(snapshot.entries)
        changed_addresses: list[str] = []
        for node in spec.nodes:
            entries[node.address] = _snapshot_entry(node.address, NODE_RESOURCE_TYPE, result)
            changed_addresses.append(node.address)
        for network in spec.networks:
            entries[network.address] = _snapshot_entry(network.address, NETWORK_RESOURCE_TYPE, result)
            changed_addresses.append(network.address)

        return ApplyResult(
            success=result.accepted,
            snapshot=snapshot.with_entries(entries),
            diagnostics=diagnostics,
            changed_addresses=changed_addresses,
        )


def _snapshot_entry(address: str, resource_type: str, result: Any) -> SnapshotEntry:
    payload: dict[str, Any] = {"request_id": result.request_id, "status": result.status}
    if result.range_id:
        payload["range_id"] = result.range_id
    return SnapshotEntry(
        address=address,
        domain=RuntimeDomain.PROVISIONING,
        resource_type=resource_type,
        payload=payload,
        status=result.status,
    )


def create_shifter_backend_components(
    *,
    manifest: BackendManifest,
    port: ShifterProvisioningDispatchPort,
    **config: Any,
) -> RuntimeTargetComponents:
    """Build the ``provisioning-only`` Shifter backend components for ``manifest``."""
    del manifest, config
    return RuntimeTargetComponents(provisioner=ShifterProvisioner(port=port))


def register_shifter_backend(registry: BackendRegistry) -> None:
    """Register the Shifter backend descriptor on ``registry``."""
    registry.register(SHIFTER_BACKEND_NAME, create_shifter_backend_manifest, create_shifter_backend_components)


def create_shifter_backend_target(*, port: ShifterProvisioningDispatchPort, **config: Any) -> RuntimeTarget:
    """Return a fully configured, provisioning-only Shifter ``RuntimeTarget``."""
    manifest = create_shifter_backend_manifest(**config)
    components = create_shifter_backend_components(manifest=manifest, port=port, **config)
    return RuntimeTarget(name=SHIFTER_BACKEND_NAME, manifest=manifest, provisioner=components.provisioner)
