"""Build a GCE range-cell plan from a serialized ACES plan (ADR-031, ADR-032).

The ACES-native counterpart of ``gcp_range_cell_plan.render_range_cell_plan``:
it maps the neutral :class:`aces_plan.AcesPlan` (parsed from the serialized ACES
ProvisioningPlan) into the provisioner's neutral ``RangeCellPlan`` so the whole
GCE apply layer (``_ensure_network``/``_ensure_subnetwork``/``_ensure_firewall``/
``_ensure_address`` + resource renderers) is reused unchanged. It carries **no**
cyberscript scenario semantics: the image comes from the authored ACES ``source``
resolved against the tenant registry (``resolve_gce_image``), sizing from
``resources``, and ``os_family`` drives only the OS realization dialect. Nodes are
placed on their authored network; each ``count`` yields a distinct instance.

Base range firewalls (intra-subnet allow, management, egress posture) are reused
from ``gcp_range_cell_plan._firewall_plan``; authored node ACLs are realized as
additional firewall rules by :mod:`aces_gcp_firewall` (layered on top).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

from aces_plan import AcesPlan, AcesPlanNetwork, AcesPlanNode
from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from gcp_range_cell_plan import (
    InstancePlan,
    RangeCellPlan,
    SubnetPlan,
    _firewall_plan,
    _network_name_from_id,
    _network_self_link,
    _network_tag,
    _range_labels,
    _short_resource_name,
    _subnet_tag,
    _subnetwork_self_link,
)

#: Default guest login user the provisioner injects (management reachability). The
#: participant-facing user is a later participant-runtime concern (not provisioning).
_DEFAULT_SSH_USERNAME = "aces"
_DEFAULT_SSH_PORT = 22


class AcesGcePlanError(RuntimeError):
    """Raised when an ACES plan cannot be realized as a GCE range-cell plan."""


def build_aces_range_cell_plan(
    request_uuid: str,
    range_id: int,
    aces_plan: AcesPlan,
    resolve_image: Callable[[AcesPlanNode], GCERangeImageProfile],
    config: GCERangeCellConfig | None = None,
) -> RangeCellPlan:
    """Render the deterministic GCE range-cell plan for a parsed ACES plan.

    ``resolve_image`` maps one node to its concrete image/sizing profile (wired to
    the tenant image registry by the caller), keeping this builder pure/testable.
    """
    resolved_config = config or load_gce_range_cell_config()
    network_name, network_link, manage_network = _network_placement(resolved_config, range_id)

    networks_by_address = {network.address: network for network in aces_plan.networks}
    nodes_by_network = _nodes_by_network(aces_plan)

    subnet_plans = [
        _subnet_plan(
            network, nodes_by_network.get(network.address, ()), range_id, resolved_config, network_name, network_link
        )
        for network in aces_plan.networks
    ]
    subnet_by_address = {
        network.address: subnet for network, subnet in zip(aces_plan.networks, subnet_plans, strict=True)
    }

    instance_plans: list[InstancePlan] = []
    for network in aces_plan.networks:
        subnet = subnet_by_address[network.address]
        for node in nodes_by_network.get(network.address, ()):
            instance_plans.extend(_instance_plans_for_node(node, subnet, range_id, resolve_image))

    _reject_unplaceable_nodes(aces_plan, networks_by_address)

    return {
        "project_id": resolved_config.project_id,
        "region": resolved_config.region,
        "zone": resolved_config.zone,
        "request_uuid": request_uuid,
        "range_id": range_id,
        "private_google_access": resolved_config.private_google_access,
        "labels": _range_labels(range_id, request_uuid),
        "network": {"name": network_name, "self_link": network_link},
        "manage_network": manage_network,
        "subnets": subnet_plans,
        "instances": instance_plans,
        "firewalls": _firewall_plan(range_id, subnet_plans, resolved_config),
    }


def _network_placement(config: GCERangeCellConfig, range_id: int) -> tuple[str, str, bool]:
    """Resolve (network_name, network_link, manage_network) for the range VPC."""
    if config.network_mode == "shared-vpc":
        return _network_name_from_id(config.network_id), config.network_id, False
    name = _short_resource_name("shifter-range", range_id)
    return name, _network_self_link(config.project_id, name), True


def _primary_network(node: AcesPlanNode) -> str | None:
    """Return the node's primary (first) declared network address, or None."""
    return node.network_addresses[0] if node.network_addresses else None


def _nodes_by_network(aces_plan: AcesPlan) -> dict[str, list[AcesPlanNode]]:
    """Group nodes by their primary network address (declaration order preserved)."""
    grouped: dict[str, list[AcesPlanNode]] = {}
    for node in aces_plan.nodes:
        primary = _primary_network(node)
        if primary is not None:
            grouped.setdefault(primary, []).append(node)
    return grouped


def _reject_unplaceable_nodes(aces_plan: AcesPlan, networks_by_address: dict[str, AcesPlanNetwork]) -> None:
    """Fail loud on nodes with no network or an undeclared primary network."""
    for node in aces_plan.nodes:
        primary = _primary_network(node)
        if primary is None:
            raise AcesGcePlanError(f"node {node.address!r} declares no network to attach to")
        if primary not in networks_by_address:
            raise AcesGcePlanError(f"node {node.address!r} references undeclared network {primary!r}")


def _usable_host_ips(cidr: str) -> list[str]:
    """Return assignable IPv4 host addresses in ``cidr`` (GCP reserves 2 at each end)."""
    network = ipaddress.ip_network(cidr)
    if not isinstance(network, ipaddress.IPv4Network):
        raise AcesGcePlanError(f"GCE range cells require IPv4 subnets, got {cidr}")
    return [str(host) for host in list(network.hosts())[2:-2]]


def _instance_keys(nodes: tuple[AcesPlanNode, ...] | list[AcesPlanNode]) -> list[str]:
    """Return the ordered per-instance assignment keys (one per node ``count``)."""
    return [_instance_key(node, index) for node in nodes for index in range(node.count)]


def _instance_key(node: AcesPlanNode, index: int) -> str:
    """Return the stable IP-assignment key for one instance of a node."""
    return f"{node.address}#{index}"


def _subnet_plan(
    network: AcesPlanNetwork,
    nodes: tuple[AcesPlanNode, ...] | list[AcesPlanNode],
    range_id: int,
    config: GCERangeCellConfig,
    network_name: str,
    network_link: str,
) -> SubnetPlan:
    """Render one subnet plan from an ACES network + the nodes placed on it."""
    if not network.cidr:
        raise AcesGcePlanError(f"ACES network {network.address!r} has no cidr to provision a GCE subnet")
    resource_name = _short_resource_name("shifter-r", range_id, network.name)
    keys = _instance_keys(nodes)
    usable = _usable_host_ips(network.cidr)
    if len(keys) > len(usable):
        raise AcesGcePlanError(
            f"subnet {network.cidr} has {len(usable)} usable addresses but {len(keys)} instances were requested"
        )
    return {
        "name": network.name,
        "uuid": network.address,
        "resource_name": resource_name,
        "self_link": _subnetwork_self_link(config.project_id, config.region, resource_name),
        "network": network_name,
        "network_link": network_link,
        "cidr": network.cidr,
        "region": config.region,
        "tag": _subnet_tag(range_id, network.name),
        # ACES segments via node ACLs (realized separately); the base firewall
        # allows intra-subnet traffic only.
        "connected_source_ranges": [network.cidr],
        "ip_assignments": dict(zip(keys, usable, strict=False)),
        "instances": [],
    }


def _instance_plans_for_node(
    node: AcesPlanNode,
    subnet: SubnetPlan,
    range_id: int,
    resolve_image: Callable[[AcesPlanNode], GCERangeImageProfile],
) -> list[InstancePlan]:
    """Render one InstancePlan per ``count`` for a node placed on ``subnet``."""
    profile = resolve_image(node)
    os_type = node.os_family or "linux"
    plans: list[InstancePlan] = []
    for index in range(node.count):
        resource_name = _short_resource_name("shifter-r", range_id, subnet["name"], node.name, index)
        plans.append(
            {
                "name": f"{node.name}-{index}" if node.count > 1 else node.name,
                "uuid": _instance_key(node, index),
                "resource_name": resource_name,
                "address_name": _short_resource_name(resource_name, "ip"),
                "subnet_name": subnet["name"],
                "subnet_resource_name": subnet["resource_name"],
                "subnetwork_link": subnet["self_link"],
                "private_ip": subnet["ip_assignments"][_instance_key(node, index)],
                # Neutral labels only: no cyberscript role/os_type enums or
                # scenario secrets. os_type carries the OS family for the guest
                # boot dialect (linux vs windows) that instance_resource keys on.
                "role": "aces-node",
                "os_type": os_type,
                "asset_type": "gce_vm",
                "tags": [_network_tag(range_id), subnet["tag"]],
                "profile": profile,
                "source": {},
                "ssh_username": _DEFAULT_SSH_USERNAME,
                "host_ssh_username": _DEFAULT_SSH_USERNAME,
                "ssh_port": _DEFAULT_SSH_PORT,
            }
        )
    return plans
