"""Resource planning helpers for GCE-backed range cells."""

from __future__ import annotations

import ipaddress
import re
from typing import NotRequired, TypedDict, cast

from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from executors.factory import get_ssh_username

_MANAGED_BY_LABEL = "shifter-provisioner"

ResourceDict = dict[str, object]
ComputeResource = dict[str, object]
ScenarioInstance = ResourceDict


class NetworkPlan(TypedDict):
    """Planned Compute Engine network resource."""

    name: str
    self_link: str


class SubnetPlan(TypedDict):
    """Planned Compute Engine subnetwork resource."""

    name: str
    uuid: str
    resource_name: str
    self_link: str
    network: str
    network_link: str
    cidr: str
    region: str
    tag: str
    connected_source_ranges: list[str]
    ip_assignments: dict[str, str]
    instances: list[ScenarioInstance]


class FirewallEntry(TypedDict, total=False):
    """Compute Engine firewall allow/deny entry."""

    IPProtocol: str
    ports: list[str]


class FirewallPlan(TypedDict):
    """Planned Compute Engine firewall resource."""

    name: str
    direction: str
    priority: int
    target_tags: list[str]
    source_ranges: NotRequired[list[str]]
    destination_ranges: NotRequired[list[str]]
    allowed: NotRequired[list[FirewallEntry]]
    denied: NotRequired[list[FirewallEntry]]


class InstancePlan(TypedDict):
    """Planned Compute Engine instance and address resources."""

    name: str
    uuid: str
    resource_name: str
    address_name: str
    subnet_name: str
    subnet_resource_name: str
    subnetwork_link: str
    private_ip: str
    role: str
    os_type: str
    asset_type: str
    tags: list[str]
    profile: GCERangeImageProfile
    source: ScenarioInstance
    ssh_username: str


class RangeCellPlan(TypedDict):
    """Complete resource plan for a single GCE range cell."""

    project_id: str
    region: str
    zone: str
    request_uuid: str
    range_id: int
    labels: dict[str, str]
    network: NetworkPlan
    subnets: list[SubnetPlan]
    instances: list[InstancePlan]
    firewalls: list[FirewallPlan]


def _resource_dicts(value: object) -> list[ResourceDict]:
    """Return dict items from a dynamic scenario payload list."""
    if not isinstance(value, list):
        return []
    return [cast(ResourceDict, item) for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    """Return string values from a dynamic scenario payload list."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _sanitize_name(value: str, *, max_length: int = 63) -> str:
    """Normalize a value into a Compute Engine resource name."""
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    normalized = normalized[:max_length].rstrip("-")
    if not normalized:
        normalized = "range"
    if not normalized[0].isalpha():
        normalized = f"r-{normalized}"
    return normalized[:max_length].rstrip("-")


def _label_value(value: object, *, max_length: int = 63) -> str:
    """Normalize a value into a Compute Engine label value."""
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value).strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    return normalized[:max_length].rstrip("-_") or "unknown"


def _short_resource_name(prefix: str, *parts: object, max_length: int = 63) -> str:
    """Build a bounded resource name from stable name parts."""
    return _sanitize_name(
        "-".join([prefix, *(str(part) for part in parts if part not in (None, ""))]), max_length=max_length
    )


def _network_self_link(project_id: str, network_name: str) -> str:
    """Return the relative self-link for a global Compute network."""
    return f"projects/{project_id}/global/networks/{network_name}"


def _subnetwork_self_link(project_id: str, region: str, subnet_name: str) -> str:
    """Return the relative self-link for a regional Compute subnet."""
    return f"projects/{project_id}/regions/{region}/subnetworks/{subnet_name}"


def _machine_type_self_link(zone: str, machine_type: str) -> str:
    """Return the relative self-link for a zonal machine type."""
    return f"zones/{zone}/machineTypes/{machine_type}"


def _disk_type_self_link(zone: str, disk_type: str) -> str:
    """Return the relative self-link for a zonal disk type."""
    return f"zones/{zone}/diskTypes/{disk_type}"


def _range_labels(range_id: int, request_uuid: str) -> dict[str, str]:
    """Return labels shared by resources in one range cell."""
    return {
        "managed-by": _MANAGED_BY_LABEL,
        "range-id": _label_value(range_id),
        "request-id": _label_value(request_uuid),
    }


def _network_tag(range_id: int) -> str:
    """Return the common network tag for a range cell."""
    return _short_resource_name("shifter-range", range_id)


def _subnet_tag(range_id: int, subnet_name: str) -> str:
    """Return the subnet-scoped network tag for range instances."""
    return _short_resource_name("shifter-range", range_id, subnet_name)


def _assign_instance_ips(subnet_cidr: str, instances: list[ScenarioInstance]) -> dict[str, str]:
    """Assign deterministic internal IPs while skipping GCP-reserved addresses."""
    network = ipaddress.ip_network(subnet_cidr)
    if not isinstance(network, ipaddress.IPv4Network):
        raise RuntimeError(f"GCE range cells require IPv4 subnets, got {subnet_cidr}")

    hosts = list(network.hosts())
    usable = hosts[2:-2]
    if len(instances) > len(usable):
        raise RuntimeError(
            f"Subnet {subnet_cidr} has {len(usable)} usable GCE guest addresses, "
            f"but {len(instances)} instances were requested"
        )

    assignments: dict[str, str] = {}
    for index, instance in enumerate(instances):
        key = str(instance.get("uuid") or instance.get("name") or f"asset-{index}")
        assignments[key] = str(usable[index])
    return assignments


def _instance_assignment_key(instance: ScenarioInstance, index: int) -> str:
    """Return the stable key used to map an instance to an assigned IP."""
    return str(instance.get("uuid") or instance.get("name") or f"asset-{index}")


def _connected_source_ranges(subnet: ResourceDict, subnet_by_name: dict[str, ResourceDict]) -> list[str]:
    """Return CIDRs allowed to reach one subnet from declared peer links."""
    source_ranges = [str(subnet.get("cidr", "")).strip()]
    for peer_name in _string_list(subnet.get("connected_to")):
        peer = subnet_by_name.get(peer_name)
        if peer:
            source_ranges.append(str(peer.get("cidr", "")).strip())
    return [cidr for cidr in source_ranges if cidr]


def _build_subnet_plans(
    *,
    variables: ResourceDict,
    config: GCERangeCellConfig,
    network_name: str,
    network_link: str,
) -> list[SubnetPlan]:
    """Render deterministic subnetwork plans from range variables."""
    range_id = int(str(variables["range_id"]))
    subnets = _resource_dicts(variables.get("subnets"))
    subnet_by_name = {str(subnet.get("name", "")): subnet for subnet in subnets}
    plans: list[SubnetPlan] = []
    for subnet in subnets:
        subnet_name = str(subnet.get("name", "")).strip()
        subnet_uuid = str(subnet.get("uuid", "")).strip()
        subnet_cidr = str(subnet.get("cidr", "")).strip()
        if not subnet_name or not subnet_uuid or not subnet_cidr:
            raise RuntimeError(f"GCE range subnet requires name, uuid, and cidr: {subnet!r}")
        instances = _resource_dicts(subnet.get("instances"))
        resource_name = _short_resource_name("shifter-r", range_id, subnet_name)
        plans.append(
            {
                "name": subnet_name,
                "uuid": subnet_uuid,
                "resource_name": resource_name,
                "self_link": _subnetwork_self_link(config.project_id, config.region, resource_name),
                "network": network_name,
                "network_link": network_link,
                "cidr": subnet_cidr,
                "region": config.region,
                "tag": _subnet_tag(range_id, subnet_name),
                "connected_source_ranges": _connected_source_ranges(subnet, subnet_by_name),
                "ip_assignments": _assign_instance_ips(subnet_cidr, instances),
                "instances": instances,
            }
        )
    return plans


def _profile_for_instance(
    config: GCERangeCellConfig,
    instance: ScenarioInstance,
    *,
    require_images: bool,
) -> GCERangeImageProfile:
    """Resolve the image profile for one range instance."""
    if not require_images:
        return GCERangeImageProfile()
    return config.get_profile(
        role=str(instance.get("role", "victim")),
        os_type=str(instance.get("os_type", instance.get("os", "ubuntu"))),
        requested_type=str(instance.get("instance_type", "")).strip(),
    )


def _build_instance_plans(
    *,
    variables: ResourceDict,
    config: GCERangeCellConfig,
    subnet_plans: list[SubnetPlan],
    require_images: bool,
) -> list[InstancePlan]:
    """Render deterministic instance plans for every planned subnet."""
    range_id = int(str(variables["range_id"]))
    plans: list[InstancePlan] = []
    for subnet_plan in subnet_plans:
        for index, instance in enumerate(subnet_plan["instances"]):
            key = _instance_assignment_key(instance, index)
            role = str(instance.get("role", "victim"))
            os_type = str(instance.get("os_type", instance.get("os", "ubuntu")))
            resource_name = _short_resource_name(
                "shifter-r",
                range_id,
                subnet_plan["name"],
                instance.get("name") or instance.get("uuid") or index,
            )
            plans.append(
                {
                    "name": str(instance.get("name", "")).strip() or resource_name,
                    "uuid": str(instance.get("uuid", "")),
                    "resource_name": resource_name,
                    "address_name": _short_resource_name(resource_name, "ip"),
                    "subnet_name": subnet_plan["name"],
                    "subnet_resource_name": subnet_plan["resource_name"],
                    "subnetwork_link": subnet_plan["self_link"],
                    "private_ip": subnet_plan["ip_assignments"][key],
                    "role": role,
                    "os_type": os_type,
                    "asset_type": "gce_vm",
                    "tags": [_network_tag(range_id), subnet_plan["tag"], _short_resource_name("shifter-role", role)],
                    "profile": _profile_for_instance(config, instance, require_images=require_images),
                    "source": instance,
                    "ssh_username": get_ssh_username(os_type, role),
                }
            )
    return plans


def _firewall_plan(
    range_id: int,
    subnet_plans: list[SubnetPlan],
    config: GCERangeCellConfig,
) -> list[FirewallPlan]:
    """Render the firewall plan for internal range traffic and management."""
    range_tag = _network_tag(range_id)
    subnet_cidrs = [subnet["cidr"] for subnet in subnet_plans]
    firewalls: list[FirewallPlan] = []
    for subnet in subnet_plans:
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, subnet["name"], "ingress"),
                "direction": "INGRESS",
                "priority": 1000,
                "target_tags": [subnet["tag"]],
                "source_ranges": subnet["connected_source_ranges"],
                "allowed": [{"IPProtocol": "all"}],
            }
        )
    if config.portal_network_cidrs:
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "mgmt"),
                "direction": "INGRESS",
                "priority": 900,
                "target_tags": [range_tag],
                "source_ranges": list(config.portal_network_cidrs),
                "allowed": [{"IPProtocol": "tcp", "ports": ["22", "3389"]}],
            }
        )
    firewalls.extend(
        [
            {
                "name": _short_resource_name("shifter-r", range_id, "egress-internal"),
                "direction": "EGRESS",
                "priority": 1000,
                "target_tags": [range_tag],
                "destination_ranges": subnet_cidrs,
                "allowed": [{"IPProtocol": "all"}],
            },
            {
                "name": _short_resource_name("shifter-r", range_id, "egress-deny"),
                "direction": "EGRESS",
                "priority": 65534,
                "target_tags": [range_tag],
                "destination_ranges": ["0.0.0.0/0"],
                "denied": [{"IPProtocol": "all"}],
            },
        ]
    )
    if config.egress_allow_cidrs:
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "egress-allow"),
                "direction": "EGRESS",
                "priority": 1100,
                "target_tags": [range_tag],
                "destination_ranges": list(config.egress_allow_cidrs),
                "allowed": [{"IPProtocol": "all"}],
            }
        )
    return firewalls


def render_range_cell_plan(
    request_uuid: str,
    variables: ResourceDict,
    config: GCERangeCellConfig | None = None,
    *,
    require_images: bool = True,
) -> RangeCellPlan:
    """Render the deterministic GCE resources for one range cell."""
    resolved_config = config or load_gce_range_cell_config()
    range_id = int(str(variables["range_id"]))
    network_name = _short_resource_name("shifter-range", range_id)
    network_link = _network_self_link(resolved_config.project_id, network_name)
    subnet_plans = _build_subnet_plans(
        variables=variables,
        config=resolved_config,
        network_name=network_name,
        network_link=network_link,
    )
    instance_plans = _build_instance_plans(
        variables=variables,
        config=resolved_config,
        subnet_plans=subnet_plans,
        require_images=require_images,
    )
    return {
        "project_id": resolved_config.project_id,
        "region": resolved_config.region,
        "zone": resolved_config.zone,
        "request_uuid": request_uuid,
        "range_id": range_id,
        "labels": _range_labels(range_id, request_uuid),
        "network": {
            "name": network_name,
            "self_link": network_link,
        },
        "subnets": subnet_plans,
        "instances": instance_plans,
        "firewalls": _firewall_plan(range_id, subnet_plans, resolved_config),
    }
