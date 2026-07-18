"""Resource planning helpers for GCE-backed range cells."""

from __future__ import annotations

import ipaddress
from typing import NotRequired, TypedDict, cast

from shared.range_cells import RangeCellContractError, validate_gcp_vm_range_cell_request

from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from gcp_range_cell_naming import (
    _label_value,
    _network_name_from_id,
    _network_self_link,
    _network_tag,
    _short_resource_name,
    _subnet_tag,
    _subnetwork_self_link,
)
from gcp_range_cell_scenario import build_instance_plans, realize_range_spec
from gcp_vpn_identity import gcp_vpn_gateway_service_account_email

_MANAGED_BY_LABEL = "shifter-provisioner"

# private.googleapis.com VIP range. Private Google Access on the range subnet,
# the range VPC's private-googleapis DNS zone, and a route for this /30 (all in
# the range VPC Terraform) let no-external-IP guests reach Google APIs over
# Google's internal fabric. This is the only egress hole the range opens when
# private_google_access is set, so guests reach Vertex AI / Cloud Storage /
# Secret Manager while staying off the general internet.
_GOOGLE_PRIVATE_API_VIP_CIDR = "199.36.153.8/30"  # NOSONAR
_NON_PUBLIC_IPV4_CIDRS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


def _public_ipv4_source_ranges() -> list[str]:
    """Return routable public IPv4 space without private/reserved sources."""
    allowed: list[ipaddress.IPv4Network] = [ipaddress.IPv4Network("0.0.0.0/0")]
    for excluded in _NON_PUBLIC_IPV4_CIDRS:
        next_allowed: list[ipaddress.IPv4Network] = []
        for network in allowed:
            if excluded.subnet_of(network):
                next_allowed.extend(network.address_exclude(excluded))
            else:
                next_allowed.append(network)
        allowed = next_allowed
    return [str(network) for network in sorted(allowed, key=lambda item: (int(item.network_address), item.prefixlen))]


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
    host_ssh_username: str
    ssh_port: int
    participant_access_channels: list[str]
    attach_service_account: bool


class OpenVpnGatewayPlan(TypedDict):
    """Request-owned OpenVPN gateway adjacent to one Kali member."""

    resource_name: str
    address_name: str
    private_ip: str
    subnet_resource_name: str
    subnetwork_link: str
    target_ref: str
    target_ip: str
    tag: str
    profile: GCERangeImageProfile
    service_account_email: str


class RangeCellPlan(TypedDict):
    """Complete resource plan for a single GCE range cell."""

    project_id: str
    region: str
    zone: str
    request_uuid: str
    range_id: int
    private_google_access: bool
    labels: dict[str, str]
    network: NetworkPlan
    # True when the range owns its VPC (vpc-per-range) and apply/destroy must
    # create/delete it. False in shared-vpc mode, where the VPC is the pre-existing
    # platform-peered range network and only per-range subnets/firewalls are owned.
    manage_network: bool
    subnets: list[SubnetPlan]
    instances: list[InstancePlan]
    firewalls: list[FirewallPlan]
    vpn_gateway: NotRequired[OpenVpnGatewayPlan]


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


def _range_labels(range_id: int, request_uuid: str) -> dict[str, str]:
    """Return labels shared by resources in one range cell."""
    return {
        "managed-by": _MANAGED_BY_LABEL,
        "range-id": _label_value(range_id),
        "request-id": _label_value(request_uuid),
    }


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
    require_images: bool,
) -> list[SubnetPlan]:
    """Render deterministic subnetwork plans from range variables.

    Provision (``require_images=True``) needs a CIDR to create the subnet and
    assign instance IPs. Destroy (``require_images=False``) deletes subnets by
    resource name, so a subnet whose CIDR was never allocated (e.g. auto-cleanup
    after a provision that failed before CIDR allocation) is tolerated with an
    empty CIDR rather than raising.
    """
    range_id = int(str(variables["range_id"]))
    subnets = _resource_dicts(variables.get("subnets"))
    subnet_by_name = {str(subnet.get("name", "")): subnet for subnet in subnets}
    plans: list[SubnetPlan] = []
    for subnet in subnets:
        subnet_name = str(subnet.get("name", "")).strip()
        subnet_uuid = str(subnet.get("uuid", "")).strip()
        subnet_cidr = str(subnet.get("cidr", "")).strip()
        if not subnet_name or not subnet_uuid:
            raise RuntimeError(f"GCE range subnet requires name and uuid: {subnet!r}")
        if require_images and not subnet_cidr:
            raise RuntimeError(f"GCE range subnet requires a cidr to provision: {subnet!r}")
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
                "ip_assignments": _assign_instance_ips(subnet_cidr, instances) if subnet_cidr else {},
                "instances": instances,
            }
        )
    return plans


def _firewall_plan(
    range_id: int,
    subnet_plans: list[SubnetPlan],
    config: GCERangeCellConfig,
    vpn_gateway: OpenVpnGatewayPlan | None = None,
) -> list[FirewallPlan]:
    """Render the firewall plan for internal range traffic and management."""
    range_tag = _network_tag(range_id)
    subnet_cidrs = [subnet["cidr"] for subnet in subnet_plans]
    portal_network_cidrs = _validated_boundary_cidrs("portal_network_cidrs", config.portal_network_cidrs)
    access_network_cidrs = _validated_boundary_cidrs("access_network_cidrs", config.access_network_cidrs)
    egress_allow_cidrs = _validated_boundary_cidrs("egress_allow_cidrs", config.egress_allow_cidrs)
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
    if access_network_cidrs:
        # Dedicated access-workload source identity (issue #1349): participant/
        # operator access (SSH 22, RDP 3389) is a rule of its own, sourced only
        # from the access-workload ranges (portal + guacd), so it is never a
        # provisioner/management wildcard. Provisioner + native/Docker-host
        # management ingress is the separate rule below on portal_network_cidrs.
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "access"),
                "direction": "INGRESS",
                "priority": 900,
                "target_tags": [range_tag],
                "source_ranges": access_network_cidrs,
                "allowed": [{"IPProtocol": "tcp", "ports": ["22", "3389"]}],
            }
        )
        if portal_network_cidrs:
            # Management-only ingress: native-guest host SSH (:22) and the
            # Docker-host management sshd port (Polaris host, whose Kali container
            # binds :22). No RDP: participant RDP is the access rule above.
            mgmt_ports = ["22"]
            if str(config.host_mgmt_ssh_port) not in mgmt_ports:
                mgmt_ports.append(str(config.host_mgmt_ssh_port))
            firewalls.append(
                {
                    "name": _short_resource_name("shifter-r", range_id, "mgmt"),
                    "direction": "INGRESS",
                    "priority": 900,
                    "target_tags": [range_tag],
                    "source_ranges": portal_network_cidrs,
                    "allowed": [{"IPProtocol": "tcp", "ports": mgmt_ports}],
                }
            )
    elif portal_network_cidrs:
        # No dedicated access-workload range configured: keep the legacy combined
        # rule (SSH participant + native-guest host, RDP, Docker-host sshd) so
        # deployments without an access node pool are unchanged.
        mgmt_ports = ["22", "3389"]
        if str(config.host_mgmt_ssh_port) not in mgmt_ports:
            mgmt_ports.append(str(config.host_mgmt_ssh_port))
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "mgmt"),
                "direction": "INGRESS",
                "priority": 900,
                "target_tags": [range_tag],
                "source_ranges": portal_network_cidrs,
                "allowed": [{"IPProtocol": "tcp", "ports": mgmt_ports}],
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
    if egress_allow_cidrs:
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "egress-allow"),
                "direction": "EGRESS",
                "priority": 1100,
                "target_tags": [range_tag],
                "destination_ranges": egress_allow_cidrs,
                "allowed": [{"IPProtocol": "all"}],
            }
        )
    if config.private_google_access:
        # Couple Private Google Access with its egress hole automatically: with
        # PGA the range VPC resolves *.googleapis.com to the private VIP and
        # routes it internally, but the per-range egress-deny still blocks it
        # without this allow. Guests reach Vertex AI (a14-kali agent), Cloud
        # Storage (smoketest tarball), and Secret Manager (per-range Vertex key)
        # over HTTPS to the VIP only, staying off the general internet.
        firewalls.append(
            {
                "name": _short_resource_name("shifter-r", range_id, "egress-googleapis"),
                "direction": "EGRESS",
                "priority": 1100,
                "target_tags": [range_tag],
                "destination_ranges": [_GOOGLE_PRIVATE_API_VIP_CIDR],
                "allowed": [{"IPProtocol": "tcp", "ports": ["443"]}],
            }
        )
    if vpn_gateway is not None:
        firewalls.extend(
            [
                {
                    "name": _short_resource_name("shifter-r", range_id, "vpn-in"),
                    "direction": "INGRESS",
                    "priority": 800,
                    "target_tags": [vpn_gateway["tag"]],
                    "source_ranges": _public_ipv4_source_ranges(),
                    "allowed": [{"IPProtocol": "udp", "ports": ["1194"]}],
                },
                {
                    "name": _short_resource_name("shifter-r", range_id, "vpn-health"),
                    "direction": "INGRESS",
                    "priority": 800,
                    "target_tags": [vpn_gateway["tag"]],
                    "source_ranges": portal_network_cidrs,
                    "allowed": [{"IPProtocol": "tcp", "ports": ["1195"]}],
                },
                {
                    "name": _short_resource_name("shifter-r", range_id, "vpn-target"),
                    "direction": "EGRESS",
                    "priority": 800,
                    "target_tags": [vpn_gateway["tag"]],
                    "destination_ranges": [f"{vpn_gateway['target_ip']}/32"],
                    "allowed": [{"IPProtocol": "all"}],
                },
                {
                    "name": _short_resource_name("shifter-r", range_id, "vpn-api"),
                    "direction": "EGRESS",
                    "priority": 800,
                    "target_tags": [vpn_gateway["tag"]],
                    "destination_ranges": [_GOOGLE_PRIVATE_API_VIP_CIDR],
                    "allowed": [{"IPProtocol": "tcp", "ports": ["443"]}],
                },
                {
                    "name": _short_resource_name("shifter-r", range_id, "vpn-deny"),
                    "direction": "EGRESS",
                    "priority": 900,
                    "target_tags": [vpn_gateway["tag"]],
                    "destination_ranges": ["0.0.0.0/0"],
                    "denied": [{"IPProtocol": "all"}],
                },
            ]
        )
    return firewalls


def _openvpn_gateway_plan(
    range_id: int,
    generation: str,
    instance_plans: list[InstancePlan],
    subnet_plans: list[SubnetPlan],
    config: GCERangeCellConfig,
    remote_access: dict[str, object] | None,
) -> OpenVpnGatewayPlan | None:
    if remote_access is None:
        return None
    targets = [instance for instance in instance_plans if instance["uuid"] == remote_access["target_ref"]]
    if (
        config.network_mode != "shared-vpc"
        or not config.private_google_access
        or not config.linux.source_image
        or not config.portal_network_cidrs
        or "https://www.googleapis.com/auth/cloud-platform" not in config.service_account_scopes
    ):
        raise RuntimeError("The authorized OpenVPN capability cannot be realized by this GCE adapter")
    if len(targets) != 1:
        raise RuntimeError("OpenVPN capability must identify exactly one GCE range member")
    target = targets[0]
    subnet = next(item for item in subnet_plans if item["name"] == target["subnet_name"])
    network = ipaddress.ip_network(subnet["cidr"])
    used = set(subnet["ip_assignments"].values())
    available = [str(address) for address in list(network.hosts())[2:-2] if str(address) not in used]
    if not available:
        raise RuntimeError("The Kali subnet has no address available for its OpenVPN gateway")
    return {
        "resource_name": _short_resource_name("shifter-r", range_id, "vpn-gateway"),
        "address_name": _short_resource_name("shifter-r", range_id, "vpn-gateway-ip"),
        "private_ip": available[0],
        "subnet_resource_name": subnet["resource_name"],
        "subnetwork_link": subnet["self_link"],
        "target_ref": target["uuid"],
        "target_ip": target["private_ip"],
        "tag": _short_resource_name("shifter-r", range_id, "vpn-gateway"),
        "profile": config.get_profile(role="victim", os_type="ubuntu"),
        "service_account_email": gcp_vpn_gateway_service_account_email(
            config.project_id,
            range_id,
            generation,
        ),
    }


def _validated_boundary_cidrs(field: str, values: tuple[str, ...]) -> list[str]:
    """Return normalized explicit IPv4 boundary exceptions, rejecting universal allows."""
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise RuntimeError(f"{field} contains an invalid network: {value}") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise RuntimeError(f"{field} must contain only IPv4 networks")
        if network.prefixlen == 0:
            raise RuntimeError(f"{field} must not include 0.0.0.0/0")
        cidr = str(network)
        if cidr not in normalized:
            normalized.append(cidr)
    return normalized


def render_range_cell_plan(
    request_uuid: str,
    variables: ResourceDict,
    config: GCERangeCellConfig | None = None,
    *,
    require_images: bool = True,
) -> RangeCellPlan:
    """Render the deterministic GCE resources for one range cell."""
    validated_request = validate_gcp_vm_range_cell_request(variables)
    operation = validated_request["operation"]
    if operation["request_id"] != request_uuid:
        raise RangeCellContractError("range-cell request_id does not match the invoked operation")
    realized_variables = realize_range_spec(
        validated_request,
        require_network_bindings=require_images,
    )
    resolved_config = config or load_gce_range_cell_config()
    range_id = int(operation["range_id"])
    if resolved_config.network_mode == "shared-vpc":
        # Range subnets live in the pre-existing, platform-peered range VPC; the
        # range never creates or deletes the VPC itself.
        network_link = resolved_config.network_id
        network_name = _network_name_from_id(resolved_config.network_id)
        manage_network = False
    else:
        network_name = _short_resource_name("shifter-range", range_id)
        network_link = _network_self_link(resolved_config.project_id, network_name)
        manage_network = True
    subnet_plans = _build_subnet_plans(
        variables=realized_variables,
        config=resolved_config,
        network_name=network_name,
        network_link=network_link,
        require_images=require_images,
    )
    instance_plans = cast(
        list[InstancePlan],
        build_instance_plans(
            range_id=range_id,
            config=resolved_config,
            subnet_plans=cast(list[ResourceDict], subnet_plans),
            access_declarations=cast(list[ResourceDict], realized_variables["access_declarations"]),
            require_images=require_images,
        ),
    )
    remote_access = validated_request["remote_access"]
    vpn_gateway = _openvpn_gateway_plan(
        range_id,
        request_uuid,
        instance_plans,
        subnet_plans,
        resolved_config,
        remote_access,
    )
    plan: RangeCellPlan = {
        "project_id": resolved_config.project_id,
        "region": resolved_config.region,
        "zone": resolved_config.zone,
        "request_uuid": request_uuid,
        "range_id": range_id,
        "private_google_access": resolved_config.private_google_access,
        "labels": _range_labels(range_id, request_uuid),
        "network": {
            "name": network_name,
            "self_link": network_link,
        },
        "manage_network": manage_network,
        "subnets": subnet_plans,
        "instances": instance_plans,
        "firewalls": _firewall_plan(range_id, subnet_plans, resolved_config, vpn_gateway),
    }
    if vpn_gateway is not None:
        plan["vpn_gateway"] = vpn_gateway
    return plan
