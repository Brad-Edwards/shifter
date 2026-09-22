"""Compute Engine API resource bodies for GCE range cells.

Field names are the google-cloud-compute (proto-plus) message field names
(snake_case), not the REST/JSON camelCase, because these dicts are passed to the
``*_resource=`` kwargs of the Compute clients, which construct the proto messages
from them. Note the proto-plus quirks ``I_p_protocol`` (REST ``IPProtocol``) and
``network_i_p`` (REST ``networkIP``).

The OpenVPN forwarding-gateway bodies live in ``_gcp_range_cell_openvpn`` and are
re-exported here, so importers see the same surface as before that split.
"""

from __future__ import annotations

from typing import Any, cast

from _gcp_range_cell_openvpn import (
    openvpn_gateway_address_resource,
    openvpn_gateway_instance_resource,
)
from config import GCERangeCellConfig
from gcp_range_cell_naming import (
    _disk_type_self_link,
    _label_value,
    _machine_type_self_link,
)
from gcp_range_cell_plan import (
    ComputeResource,
    FirewallPlan,
    InstancePlan,
    RangeCellPlan,
    SubnetPlan,
)
from guest_host_keys import linux_host_key_script as _linux_host_key_script
from guest_host_keys import windows_host_key_script as _windows_boot_script

__all__ = [
    "HOST_PUBLIC_KEY_METADATA_KEY",
    "address_resource",
    "firewall_resource",
    "instance_resource",
    "network_resource",
    "openvpn_gateway_address_resource",
    "openvpn_gateway_instance_resource",
    "router_nat_resource",
    "subnetwork_resource",
]


# Compute network, subnetwork, firewall, and address resources are NOT labelable
# (the proto has no `labels` field); only instances/disks carry range labels.
def network_resource(plan: RangeCellPlan) -> ComputeResource:
    """Render a Compute Engine network insert body."""
    return {
        "name": plan["network"]["name"],
        "auto_create_subnetworks": False,
        "routing_config": {"routing_mode": "REGIONAL"},
    }


def subnetwork_resource(plan: RangeCellPlan, subnet: SubnetPlan) -> ComputeResource:
    """Render a Compute Engine subnetwork insert body."""
    return {
        "name": subnet["resource_name"],
        "network": subnet["network_link"],
        "ip_cidr_range": subnet["cidr"],
        "region": plan["region"],
        "private_ip_google_access": plan["private_google_access"],
    }


def router_nat_resource(plan: RangeCellPlan) -> ComputeResource:
    """Render a range-owned Cloud Router + Cloud NAT insert body (PLAT-238, ADR-026-R6).

    The NAT is scoped to exactly this range's participant subnets
    (``LIST_OF_SUBNETWORKS``) with automatic NAT-IP allocation, so a range's egress
    path is range-owned and independent -- a ``none`` range simply has no router/NAT
    and therefore no NAT path, and range jobs never patch a shared Terraform-owned
    NAT object concurrently.
    """
    router_nat = plan["router_nat"]
    return {
        "name": router_nat["router_name"],
        "network": plan["network"]["self_link"],
        "region": plan["region"],
        "nats": [
            {
                "name": router_nat["nat_name"],
                "nat_ip_allocate_option": "AUTO_ONLY",
                "source_subnetwork_ip_ranges_to_nat": "LIST_OF_SUBNETWORKS",
                "subnetworks": [
                    {"name": self_link, "source_ip_ranges_to_nat": ["ALL_IP_RANGES"]}
                    for self_link in router_nat["subnetwork_self_links"]
                ],
            }
        ],
    }


def firewall_resource(plan: RangeCellPlan, firewall: FirewallPlan) -> ComputeResource:
    """Render a Compute Engine firewall insert body."""
    body: ComputeResource = {
        "name": firewall["name"],
        "network": plan["network"]["self_link"],
        "direction": firewall["direction"],
        "priority": firewall["priority"],
        "target_tags": firewall["target_tags"],
    }
    for cidr_key in ("source_ranges", "destination_ranges"):
        value = firewall.get(cidr_key)
        if value:
            body[cidr_key] = value
    for rule_key in ("allowed", "denied"):
        rules = firewall.get(rule_key)
        if rules:
            body[rule_key] = [_firewall_rule(rule) for rule in cast("list[dict[str, Any]]", rules)]
    return body


def _firewall_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Translate a firewall rule to the proto field names (IPProtocol -> I_p_protocol)."""
    translated: dict[str, Any] = {}
    for field, proto_field in (("IPProtocol", "I_p_protocol"), ("ports", "ports")):
        if field in rule:
            translated[proto_field] = rule[field]
    return translated


def address_resource(instance: InstancePlan) -> ComputeResource:
    """Render a Compute Engine internal address insert body."""
    return {
        "name": instance["address_name"],
        "address_type": "INTERNAL",
        "address": instance["private_ip"],
        "subnetwork": instance["subnetwork_link"],
    }


# Metadata key carrying the provisioner-issued SSH host public key, so a reconcile
# (instance already exists) can recover the host key that was injected at create
# time instead of minting a fresh one that would not match the running guest.
HOST_PUBLIC_KEY_METADATA_KEY = "shifter-host-public-key"


def _metadata_items(
    config: GCERangeCellConfig,
    instance: InstancePlan,
    username: str,
    public_key: str,
    *,
    host_private_key_b64: str,
    host_public_key: str,
    composition_script: str = "",
) -> list[dict[str, str]]:
    """Render guest metadata: provisioned user key, host key install, host pubkey.

    ``composition_script`` (empty on the cyberscript path) is appended to the guest
    startup script after the host-key install, so the RAES-native path realizes
    node content/features/accounts as part of the same idempotent bootstrap. The
    guest boot dialect keys on ``instance["os_type"]`` and the linux host-key
    converge check on ``instance["ssh_port"]`` (the host management sshd port).
    """
    os_type = instance["os_type"]
    items = [{"key": key, "value": value} for key, value in config.metadata_items]
    items.append({"key": "ssh-keys", "value": f"{username}:{public_key}"})
    if host_public_key:
        items.append({"key": HOST_PUBLIC_KEY_METADATA_KEY, "value": host_public_key})
    if os_type == "windows":
        boot = _windows_boot_script(host_private_key_b64, public_key, int(instance["ssh_port"])) + composition_script
        items.append({"key": "windows-startup-script-ps1", "value": boot})
    elif host_private_key_b64:
        items.append(
            {
                "key": "startup-script",
                "value": _linux_host_key_script(host_private_key_b64, int(instance["ssh_port"])) + composition_script,
            }
        )
    return items


def instance_resource(
    plan: RangeCellPlan,
    instance: InstancePlan,
    config: GCERangeCellConfig,
    *,
    ssh_public_key: str,
    host_private_key_b64: str = "",
    host_public_key: str = "",
    composition_script: str = "",
) -> ComputeResource:
    """Render a Compute Engine instance insert body."""
    profile = instance["profile"]
    body: ComputeResource = {
        "name": instance["resource_name"],
        "machine_type": _machine_type_self_link(plan["zone"], profile.machine_type),
        "labels": {
            **plan["labels"],
            "subnet": _label_value(instance["subnet_name"]),
            "role": _label_value(instance["role"]),
            "image-key": _label_value(instance["image_key"] or "default"),
            "image-profile": instance["image_profile_fingerprint"],
        },
        "tags": {"items": instance["tags"]},
        # Install the provisioned key for the host OS login user the provisioner
        # drives (host_ssh_username), not the participant-facing user. For a
        # Docker-host guest the participant user (e.g. "kali") belongs to the
        # published container, whose authorized_keys the range bootstrap sets;
        # the host OS user (e.g. "ubuntu") is what guest setup connects as. For
        # native guests the two are identical.
        "metadata": {
            "items": _metadata_items(
                config,
                instance,
                instance["host_ssh_username"],
                ssh_public_key,
                host_private_key_b64=host_private_key_b64,
                host_public_key=host_public_key,
                composition_script=composition_script,
            )
        },
        "network_interfaces": [
            {
                "subnetwork": instance["subnetwork_link"],
                "network_i_p": instance["private_ip"],
            }
        ],
        "deletion_protection": False,
        "can_ip_forward": False,
    }
    if profile.source_machine_image:
        # The machine image supplies every captured disk. Network, metadata,
        # identity, labels, tags, machine type, and external-IP posture are all
        # explicitly replaced by the body above.
        body["advanced_machine_features"] = {"enable_nested_virtualization": True}
    else:
        body["disks"] = [
            {
                "boot": True,
                "auto_delete": True,
                "initialize_params": {
                    "source_image": profile.source_image,
                    "disk_size_gb": int(profile.disk_size_gb),
                    "disk_type": _disk_type_self_link(plan["zone"], profile.disk_type),
                },
            }
        ]
        body["shielded_instance_config"] = {
            "enable_secure_boot": True,
            "enable_vtpm": True,
            "enable_integrity_monitoring": True,
        }
    service_account_email = str(instance.get("service_account_email") or "")
    if not service_account_email and config.service_account_email and instance["attach_service_account"]:
        service_account_email = config.service_account_email
    if service_account_email:
        body["service_accounts"] = [
            {
                "email": service_account_email,
                "scopes": list(config.service_account_scopes),
            }
        ]
    elif profile.source_machine_image:
        # An omitted field inherits the captured machine-image identity. Send an
        # explicit empty list when this range node has no authorized runtime
        # identity so the bake-time service account is never attached.
        body["service_accounts"] = []
    return body
