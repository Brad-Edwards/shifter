"""Fail-closed admission for RAES 3.5 static network addresses (#2219)."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping

from raes_contracts.diagnostics import Diagnostic
from raes_contracts.planning import PlannedResource

INVALID_STATIC_NETWORK_ADDRESS_CODE = "shifter-provisioner.invalid-static-network-address"


def _infrastructure(payload: Mapping[str, object]) -> Mapping[str, object]:
    spec = payload.get("spec")
    infrastructure = spec.get("infrastructure") if isinstance(spec, Mapping) else None
    return infrastructure if isinstance(infrastructure, Mapping) else {}


def _network_handles(
    resources: list[tuple[PlannedResource, Mapping[str, object]]],
) -> tuple[dict[str, str], dict[str, ipaddress.IPv4Network | None]]:
    lookup: dict[str, str] = {}
    networks: dict[str, ipaddress.IPv4Network | None] = {}
    for resource, payload in resources:
        name = payload.get("name") or payload.get("node_name")
        for handle in (resource.address, name, resource.address.rsplit(".", 1)[-1]):
            if isinstance(handle, str) and handle:
                lookup[handle] = resource.address
        properties = _infrastructure(payload).get("properties")
        cidr = properties.get("cidr") if isinstance(properties, Mapping) else None
        try:
            parsed = ipaddress.ip_network(cidr, strict=True) if isinstance(cidr, str) else None
        except ValueError:
            parsed = None
        networks[resource.address] = parsed if isinstance(parsed, ipaddress.IPv4Network) else None
    return lookup, networks


def _node_networks(payload: Mapping[str, object], lookup: Mapping[str, str]) -> tuple[str, ...]:
    infrastructure = _infrastructure(payload)
    raw = infrastructure.get("networks")
    if raw is None:
        raw = infrastructure.get("links")
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(lookup[ref] for ref in raw if isinstance(ref, str) and ref in lookup)


def static_network_address_diagnostics(
    node_resources: list[tuple[PlannedResource, Mapping[str, object]]],
    network_resources: list[tuple[PlannedResource, Mapping[str, object]]],
    diagnostic_factory: Callable[[str, str, str], Diagnostic],
) -> list[Diagnostic]:
    """Validate static address shape and ownership before plan dispatch."""
    lookup, networks = _network_handles(network_resources)
    diagnostics: list[Diagnostic] = []
    claimed: set[tuple[str, ipaddress.IPv4Address]] = set()

    def reject(resource: PlannedResource, message: str) -> None:
        diagnostics.append(diagnostic_factory(INVALID_STATIC_NETWORK_ADDRESS_CODE, resource.address, message))

    for resource, payload in node_resources:
        infrastructure = _infrastructure(payload)
        properties = infrastructure.get("properties")
        if properties in (None, []):
            continue
        if not isinstance(properties, list | tuple):
            reject(resource, "static network properties must be a list of one-network address entries")
            continue
        count = payload.get("count", 1)
        if count != 1:
            reject(resource, "static network addressing requires exactly one node instance")
            continue
        attached = _node_networks(payload, lookup)
        primary = attached[0] if attached else None
        node_networks: set[str] = set()
        for entry in properties:
            if not isinstance(entry, Mapping) or len(entry) != 1:
                reject(resource, "each static network property must name exactly one network and IPv4 address")
                continue
            ref, raw_address = next(iter(entry.items()))
            network_address = lookup.get(ref) if isinstance(ref, str) else None
            if network_address is None or network_address != primary or network_address in node_networks:
                reject(resource, "static network address must uniquely target the node's primary network")
                continue
            try:
                address = ipaddress.ip_address(raw_address) if isinstance(raw_address, str) else None
            except ValueError:
                address = None
            network = networks.get(network_address)
            if not isinstance(address, ipaddress.IPv4Address) or network is None or address not in network:
                reject(resource, "static network address must be an assignable IPv4 host on its authored network")
                continue
            offset = int(address) - int(network.network_address)
            if offset < 3 or offset > network.num_addresses - 4:
                reject(resource, "static network address is reserved or unavailable on the target provider")
                continue
            claim = (network_address, address)
            if claim in claimed:
                reject(resource, "static network address is already claimed by another node")
                continue
            claimed.add(claim)
            node_networks.add(network_address)
    return diagnostics
