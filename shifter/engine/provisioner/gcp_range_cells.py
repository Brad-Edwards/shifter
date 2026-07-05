"""Compute Engine range-cell backend for live-fire GCP ranges."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any as ExternalValue

from cloud.gcp.base import import_google_module
from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from executors.factory import get_ssh_username
from gcp_guest_secrets import (
    delete_rdp_password_secret,
    delete_ssh_secret,
    ensure_rdp_password_secret,
    ensure_ssh_secret,
)
from log_redact import safe_log_fingerprint

logger = logging.getLogger(__name__)

_MANAGED_BY_LABEL = "shifter-provisioner"
_COMPUTE_MODULE = "google.cloud.compute_v1"
_GOOGLE_EXCEPTIONS_MODULE = "google.api_core.exceptions"
_OPERATION_TIMEOUT_SECONDS = 600
ResourceDict = dict[str, ExternalValue]


@dataclass(frozen=True)
class GCEClients:
    """Compute Engine clients used by the range-cell backend."""

    networks: ExternalValue
    subnetworks: ExternalValue
    firewalls: ExternalValue
    addresses: ExternalValue
    instances: ExternalValue
    global_operations: ExternalValue
    region_operations: ExternalValue
    zone_operations: ExternalValue
    google_exceptions: ExternalValue


@dataclass(frozen=True)
class GCEGuestSecretOps:
    """Guest credential operations used by the GCE range-cell backend."""

    ensure_ssh: Callable[[int, ResourceDict], tuple[str, str]]
    ensure_rdp_password: Callable[[int, ResourceDict], tuple[str, str]]
    delete_ssh: Callable[[int, ResourceDict], None]
    delete_rdp_password: Callable[[int, ResourceDict], None]


def _default_secret_ops() -> GCEGuestSecretOps:
    """Return the production guest-secret operation bindings."""
    return GCEGuestSecretOps(
        ensure_ssh=ensure_ssh_secret,
        ensure_rdp_password=ensure_rdp_password_secret,
        delete_ssh=delete_ssh_secret,
        delete_rdp_password=delete_rdp_password_secret,
    )


def _build_clients() -> GCEClients:
    """Build production Compute Engine clients lazily."""
    compute = import_google_module(_COMPUTE_MODULE)
    google_exceptions = import_google_module(_GOOGLE_EXCEPTIONS_MODULE)
    return GCEClients(
        networks=compute.NetworksClient(),
        subnetworks=compute.SubnetworksClient(),
        firewalls=compute.FirewallsClient(),
        addresses=compute.AddressesClient(),
        instances=compute.InstancesClient(),
        global_operations=compute.GlobalOperationsClient(),
        region_operations=compute.RegionOperationsClient(),
        zone_operations=compute.ZoneOperationsClient(),
        google_exceptions=google_exceptions,
    )


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


def _label_value(value: str, *, max_length: int = 63) -> str:
    """Normalize a value into a Compute Engine label value."""
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
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
        "range-id": _label_value(str(range_id)),
        "request-id": _label_value(request_uuid),
    }


def _network_tag(range_id: int) -> str:
    """Return the common network tag for a range cell."""
    return _short_resource_name("shifter-range", range_id)


def _subnet_tag(range_id: int, subnet_name: str) -> str:
    """Return the subnet-scoped network tag for range instances."""
    return _short_resource_name("shifter-range", range_id, subnet_name)


def _assign_instance_ips(subnet_cidr: str, instances: list[ResourceDict]) -> dict[str, str]:
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


def _instance_assignment_key(instance: ResourceDict, index: int) -> str:
    """Return the stable key used to map an instance to an assigned IP."""
    return str(instance.get("uuid") or instance.get("name") or f"asset-{index}")


def _connected_source_ranges(subnet: ResourceDict, subnet_by_name: dict[str, ResourceDict]) -> list[str]:
    """Return CIDRs allowed to reach one subnet from declared peer links."""
    source_ranges = [str(subnet.get("cidr", "")).strip()]
    for peer_name in subnet.get("connected_to", []):
        peer = subnet_by_name.get(str(peer_name))
        if peer:
            source_ranges.append(str(peer.get("cidr", "")).strip())
    return [cidr for cidr in source_ranges if cidr]


def _build_subnet_plans(
    *,
    variables: ResourceDict,
    config: GCERangeCellConfig,
    network_name: str,
    network_link: str,
) -> list[ResourceDict]:
    """Render deterministic subnetwork plans from range variables."""
    range_id = int(variables["range_id"])
    subnet_by_name = {str(subnet.get("name", "")): subnet for subnet in variables.get("subnets", [])}
    plans: list[ResourceDict] = []
    for subnet in variables.get("subnets", []):
        subnet_name = str(subnet.get("name", "")).strip()
        subnet_uuid = str(subnet.get("uuid", "")).strip()
        subnet_cidr = str(subnet.get("cidr", "")).strip()
        if not subnet_name or not subnet_uuid or not subnet_cidr:
            raise RuntimeError(f"GCE range subnet requires name, uuid, and cidr: {subnet!r}")
        resource_name = _short_resource_name("shifter-r", range_id, subnet_name)
        tag = _subnet_tag(range_id, subnet_name)
        instances = list(subnet.get("instances") or [])
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
                "tag": tag,
                "connected_source_ranges": _connected_source_ranges(subnet, subnet_by_name),
                "ip_assignments": _assign_instance_ips(subnet_cidr, instances),
                "instances": instances,
            }
        )
    return plans


def _profile_for_instance(
    config: GCERangeCellConfig,
    instance: ResourceDict,
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
    subnet_plans: list[ResourceDict],
    require_images: bool,
) -> list[ResourceDict]:
    """Render deterministic instance plans for every planned subnet."""
    range_id = int(variables["range_id"])
    plans: list[ResourceDict] = []
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


def render_range_cell_plan(
    request_uuid: str,
    variables: ResourceDict,
    config: GCERangeCellConfig | None = None,
    *,
    require_images: bool = True,
) -> ResourceDict:
    """Render the deterministic GCE resources for one range cell."""
    resolved_config = config or load_gce_range_cell_config()
    range_id = int(variables["range_id"])
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


def _firewall_plan(
    range_id: int,
    subnet_plans: list[ResourceDict],
    config: GCERangeCellConfig,
) -> list[ResourceDict]:
    """Render the firewall plan for internal range traffic and management."""
    range_tag = _network_tag(range_id)
    subnet_cidrs = [subnet["cidr"] for subnet in subnet_plans]
    firewalls: list[ResourceDict] = []
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


def _network_resource(plan: ResourceDict) -> ResourceDict:
    """Render a Compute Engine network insert body."""
    return {
        "name": plan["network"]["name"],
        "autoCreateSubnetworks": False,
        "routingConfig": {"routingMode": "REGIONAL"},
        "labels": plan["labels"],
    }


def _subnetwork_resource(plan: ResourceDict, subnet: ResourceDict) -> ResourceDict:
    """Render a Compute Engine subnetwork insert body."""
    return {
        "name": subnet["resource_name"],
        "network": subnet["network_link"],
        "ipCidrRange": subnet["cidr"],
        "region": plan["region"],
        "privateIpGoogleAccess": False,
        "labels": plan["labels"],
    }


def _firewall_resource(plan: ResourceDict, firewall: ResourceDict) -> ResourceDict:
    """Render a Compute Engine firewall insert body."""
    body = {
        "name": firewall["name"],
        "network": plan["network"]["self_link"],
        "direction": firewall["direction"],
        "priority": firewall["priority"],
        "targetTags": firewall["target_tags"],
        "labels": plan["labels"],
    }
    for key, api_key in (
        ("source_ranges", "sourceRanges"),
        ("destination_ranges", "destinationRanges"),
        ("allowed", "allowed"),
        ("denied", "denied"),
    ):
        value = firewall.get(key)
        if value:
            body[api_key] = value
    return body


def _address_resource(plan: ResourceDict, instance: ResourceDict) -> ResourceDict:
    """Render a Compute Engine internal address insert body."""
    return {
        "name": instance["address_name"],
        "addressType": "INTERNAL",
        "address": instance["private_ip"],
        "subnetwork": instance["subnetwork_link"],
        "labels": plan["labels"],
    }


def _metadata_items(config: GCERangeCellConfig, username: str, public_key: str) -> list[dict[str, str]]:
    """Render guest metadata items including the provisioned SSH public key."""
    items = [{"key": key, "value": value} for key, value in config.metadata_items]
    items.append({"key": "ssh-keys", "value": f"{username}:{public_key}"})
    return items


def _instance_resource(
    plan: ResourceDict,
    instance: ResourceDict,
    config: GCERangeCellConfig,
    *,
    ssh_public_key: str,
) -> ResourceDict:
    """Render a Compute Engine instance insert body."""
    profile: GCERangeImageProfile = instance["profile"]
    body: ResourceDict = {
        "name": instance["resource_name"],
        "machineType": _machine_type_self_link(plan["zone"], profile.machine_type),
        "labels": {
            **plan["labels"],
            "subnet": _label_value(instance["subnet_name"]),
            "role": _label_value(instance["role"]),
        },
        "tags": {"items": instance["tags"]},
        "metadata": {"items": _metadata_items(config, instance["ssh_username"], ssh_public_key)},
        "networkInterfaces": [
            {
                "subnetwork": instance["subnetwork_link"],
                "networkIP": instance["private_ip"],
            }
        ],
        "disks": [
            {
                "boot": True,
                "autoDelete": True,
                "initializeParams": {
                    "sourceImage": profile.source_image,
                    "diskSizeGb": str(profile.disk_size_gb),
                    "diskType": _disk_type_self_link(plan["zone"], profile.disk_type),
                },
            }
        ],
        "shieldedInstanceConfig": {
            "enableSecureBoot": True,
            "enableVtpm": True,
            "enableIntegrityMonitoring": True,
        },
        "deletionProtection": False,
    }
    if config.service_account_email:
        body["serviceAccounts"] = [
            {
                "email": config.service_account_email,
                "scopes": list(config.service_account_scopes),
            }
        ]
    return body


def _operation_name(operation: ExternalValue) -> str:
    """Extract a Compute operation name from SDK or dict responses."""
    if isinstance(operation, dict):
        return str(operation.get("name", ""))
    return str(getattr(operation, "name", "") or "")


def _get_operation_field(operation: ExternalValue, name: str) -> ExternalValue:
    """Read an operation field from SDK or dict responses."""
    if isinstance(operation, dict):
        return operation.get(name)
    return getattr(operation, name, None)


def _operation_error_messages(operation: ExternalValue) -> list[str]:
    """Extract provider error messages from a completed operation."""
    error = _get_operation_field(operation, "error")
    if not error:
        return []
    entries = _get_operation_field(error, "errors") if not isinstance(error, dict) else error.get("errors")
    if not entries:
        return [str(error)]
    messages: list[str] = []
    for entry in entries:
        code = _get_operation_field(entry, "code")
        message = _get_operation_field(entry, "message")
        if code and message:
            messages.append(f"{code}: {message}")
        elif message:
            messages.append(str(message))
        else:
            messages.append(str(entry))
    return messages


def _raise_for_operation_errors(operation: ExternalValue, *, operation_name: str, scope: str) -> None:
    """Raise when Compute reports errors on a completed operation."""
    errors = _operation_error_messages(operation)
    if errors:
        detail = "; ".join(errors)
        raise RuntimeError(f"GCE {scope} operation {operation_name or '<unknown>'} failed: {detail}")


def _wait_for_operation(plan: ResourceDict, clients: GCEClients, operation: ExternalValue, scope: str) -> None:
    """Wait for a Compute operation and surface asynchronous failures."""
    if operation is None:
        return
    if hasattr(operation, "result"):
        result = operation.result(timeout=_OPERATION_TIMEOUT_SECONDS)
        _raise_for_operation_errors(result or operation, operation_name=_operation_name(operation), scope=scope)
        return
    operation_name = _operation_name(operation)
    if not operation_name:
        _raise_for_operation_errors(operation, operation_name="", scope=scope)
        return
    result = None
    if scope == "global":
        result = clients.global_operations.wait(project=plan["project_id"], operation=operation_name)
    elif scope == "region":
        result = clients.region_operations.wait(
            project=plan["project_id"], region=plan["region"], operation=operation_name
        )
    elif scope == "zone":
        result = clients.zone_operations.wait(project=plan["project_id"], zone=plan["zone"], operation=operation_name)
    _raise_for_operation_errors(result or operation, operation_name=operation_name, scope=scope)


def _get_or_none(
    callable_obj: Callable[..., ExternalValue],
    exceptions: ExternalValue,
    **kwargs: ExternalValue,
) -> ExternalValue | None:
    """Return a Compute resource or None when the provider reports NotFound."""
    try:
        return callable_obj(**kwargs)
    except exceptions.NotFound:
        return None


def _ensure_network(plan: ResourceDict, clients: GCEClients) -> None:
    """Create the range VPC if it is missing."""
    name = plan["network"]["name"]
    existing = _get_or_none(clients.networks.get, clients.google_exceptions, project=plan["project_id"], network=name)
    if existing is not None:
        logger.info("GCE range network exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.networks.insert(project=plan["project_id"], network_resource=_network_resource(plan))
    _wait_for_operation(plan, clients, operation, "global")


def _ensure_subnetwork(plan: ResourceDict, clients: GCEClients, subnet: ResourceDict) -> None:
    """Create a range subnetwork if it is missing."""
    name = subnet["resource_name"]
    existing = _get_or_none(
        clients.subnetworks.get,
        clients.google_exceptions,
        project=plan["project_id"],
        region=plan["region"],
        subnetwork=name,
    )
    if existing is not None:
        logger.info("GCE range subnetwork exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.subnetworks.insert(
        project=plan["project_id"],
        region=plan["region"],
        subnetwork_resource=_subnetwork_resource(plan, subnet),
    )
    _wait_for_operation(plan, clients, operation, "region")


def _ensure_firewall(plan: ResourceDict, clients: GCEClients, firewall: ResourceDict) -> None:
    """Create one range firewall rule if it is missing."""
    name = firewall["name"]
    existing = _get_or_none(clients.firewalls.get, clients.google_exceptions, project=plan["project_id"], firewall=name)
    if existing is not None:
        logger.info("GCE range firewall exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.firewalls.insert(
        project=plan["project_id"], firewall_resource=_firewall_resource(plan, firewall)
    )
    _wait_for_operation(plan, clients, operation, "global")


def _ensure_address(plan: ResourceDict, clients: GCEClients, instance: ResourceDict) -> None:
    """Reserve an internal address for one range instance."""
    name = instance["address_name"]
    existing = _get_or_none(
        clients.addresses.get,
        clients.google_exceptions,
        project=plan["project_id"],
        region=plan["region"],
        address=name,
    )
    if existing is not None:
        logger.info("GCE range address exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.addresses.insert(
        project=plan["project_id"],
        region=plan["region"],
        address_resource=_address_resource(plan, instance),
    )
    _wait_for_operation(plan, clients, operation, "region")


def _ensure_instance(
    plan: ResourceDict,
    clients: GCEClients,
    config: GCERangeCellConfig,
    instance: ResourceDict,
    secret_ops: GCEGuestSecretOps,
) -> tuple[str, str | None]:
    """Create one range instance and its guest credential secrets."""
    name = instance["resource_name"]
    existing = _get_or_none(
        clients.instances.get,
        clients.google_exceptions,
        project=plan["project_id"],
        zone=plan["zone"],
        instance=name,
    )
    secret_ref, public_key = secret_ops.ensure_ssh(plan["range_id"], instance["source"])
    rdp_password_secret_ref: str | None = None
    if instance["role"] != "dc":
        rdp_password_secret_ref, _password = secret_ops.ensure_rdp_password(plan["range_id"], instance["source"])
    if existing is not None:
        logger.info("GCE range instance exists name_fp=%s", safe_log_fingerprint(name))
        return secret_ref, rdp_password_secret_ref

    operation = clients.instances.insert(
        project=plan["project_id"],
        zone=plan["zone"],
        instance_resource=_instance_resource(plan, instance, config, ssh_public_key=public_key),
    )
    _wait_for_operation(plan, clients, operation, "zone")
    return secret_ref, rdp_password_secret_ref


def _instance_output(
    plan: ResourceDict,
    instance: ResourceDict,
    *,
    ssh_secret_ref: str,
    rdp_password_secret_ref: str | None,
    config: GCERangeCellConfig,
) -> ResourceDict:
    """Render the provisioner output for one created instance."""
    output = {
        "uuid": instance["uuid"],
        "name": instance["name"],
        "asset_type": "gce_vm",
        "role": instance["role"],
        "os": instance["os_type"],
        "subnet_name": instance["subnet_name"],
        "instance_id": instance["resource_name"],
        "private_ip": instance["private_ip"],
        "ssh_key_secret_arn": ssh_secret_ref,
        "ssh_username": instance["ssh_username"],
        "gcp_project_id": plan["project_id"],
        "gcp_region": plan["region"],
        "gcp_zone": plan["zone"],
        "gcp_network_name": plan["network"]["name"],
        "gcp_network_self_link": plan["network"]["self_link"],
        "gcp_subnetwork_name": instance["subnet_resource_name"],
        "gcp_subnetwork_self_link": instance["subnetwork_link"],
        "gcp_instance_name": instance["resource_name"],
        "gcp_address_name": instance["address_name"],
        "gcp_network_tags": instance["tags"],
        "gcp_service_account_email": config.service_account_email,
    }
    if rdp_password_secret_ref:
        output["rdp_password_secret_arn"] = rdp_password_secret_ref
        output["gcp_rdp_password_secret_ref"] = rdp_password_secret_ref
    return output


def _subnet_outputs(plan: ResourceDict) -> dict[str, ResourceDict]:
    """Render provisioner subnet outputs keyed by scenario subnet name."""
    return {
        subnet["name"]: {
            "uuid": subnet["uuid"],
            "subnet_id": subnet["resource_name"],
            "subnet_cidr": subnet["cidr"],
            "gcp_network_name": plan["network"]["name"],
            "gcp_network_self_link": plan["network"]["self_link"],
            "gcp_subnetwork_name": subnet["resource_name"],
            "gcp_subnetwork_self_link": subnet["self_link"],
            "gcp_region": plan["region"],
            "gcp_gateway_reserved": True,
            "gcp_instance_ip_assignments": subnet["ip_assignments"],
        }
        for subnet in plan["subnets"]
    }


def apply_range_cell(
    request_uuid: str,
    variables: ResourceDict,
    *,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: GCEGuestSecretOps | None = None,
    cleanup_range_cell: Callable[[str, ResourceDict | None], None] | None = None,
) -> ResourceDict:
    """Create or reconcile a live-fire GCE range cell and return provisioner outputs."""
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    plan = render_range_cell_plan(request_uuid, variables, resolved_config)
    instance_outputs: list[ResourceDict] = []
    try:
        _ensure_network(plan, resolved_clients)
        for subnet in plan["subnets"]:
            _ensure_subnetwork(plan, resolved_clients, subnet)
        for firewall in plan["firewalls"]:
            _ensure_firewall(plan, resolved_clients, firewall)
        for instance in plan["instances"]:
            _ensure_address(plan, resolved_clients, instance)
            ssh_secret_ref, rdp_password_secret_ref = _ensure_instance(
                plan,
                resolved_clients,
                resolved_config,
                instance,
                resolved_secret_ops,
            )
            instance_outputs.append(
                _instance_output(
                    plan,
                    instance,
                    ssh_secret_ref=ssh_secret_ref,
                    rdp_password_secret_ref=rdp_password_secret_ref,
                    config=resolved_config,
                )
            )
    except Exception:
        logger.exception("GCE range-cell apply failed; attempting cleanup request_id=%s", request_uuid)
        cleanup = cleanup_range_cell or (
            lambda cleanup_request_uuid, cleanup_variables: destroy_range_cell(
                cleanup_request_uuid,
                cleanup_variables,
                config=resolved_config,
                clients=resolved_clients,
                secret_ops=resolved_secret_ops,
            )
        )
        cleanup(request_uuid, variables)
        raise
    return {"subnets": _subnet_outputs(plan), "instances": instance_outputs}


def _delete_resource(
    plan: ResourceDict,
    clients: GCEClients,
    getter: Callable[..., ExternalValue],
    deleter: Callable[..., ExternalValue],
    scope: str,
    **kwargs: ExternalValue,
) -> None:
    """Delete a Compute resource when it exists."""
    name = str(next(reversed(kwargs.values())))
    existing = _get_or_none(getter, clients.google_exceptions, **kwargs)
    if existing is None:
        return
    operation = deleter(**kwargs)
    _wait_for_operation(plan, clients, operation, scope)
    logger.info("Deleted GCE range resource name_fp=%s", safe_log_fingerprint(name))


def destroy_range_cell(
    request_uuid: str,
    variables: ResourceDict | None,
    *,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: GCEGuestSecretOps | None = None,
) -> None:
    """Destroy every GCE resource owned by one range cell."""
    if not variables:
        logger.info("No GCE range variables provided for request %s; nothing to destroy", request_uuid)
        return
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    plan = render_range_cell_plan(request_uuid, variables, resolved_config, require_images=False)

    for instance in reversed(plan["instances"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.instances.get,
            resolved_clients.instances.delete,
            "zone",
            project=plan["project_id"],
            zone=plan["zone"],
            instance=instance["resource_name"],
        )
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.addresses.get,
            resolved_clients.addresses.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            address=instance["address_name"],
        )
        resolved_secret_ops.delete_ssh(plan["range_id"], instance["source"])
        resolved_secret_ops.delete_rdp_password(plan["range_id"], instance["source"])

    for firewall in reversed(plan["firewalls"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.firewalls.get,
            resolved_clients.firewalls.delete,
            "global",
            project=plan["project_id"],
            firewall=firewall["name"],
        )

    for subnet in reversed(plan["subnets"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.subnetworks.get,
            resolved_clients.subnetworks.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            subnetwork=subnet["resource_name"],
        )

    _delete_resource(
        plan,
        resolved_clients,
        resolved_clients.networks.get,
        resolved_clients.networks.delete,
        "global",
        project=plan["project_id"],
        network=plan["network"]["name"],
    )
