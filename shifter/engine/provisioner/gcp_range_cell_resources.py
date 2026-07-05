"""Compute Engine API resource bodies for GCE range cells."""

from __future__ import annotations

from config import GCERangeCellConfig
from gcp_range_cell_plan import (
    ComputeResource,
    FirewallPlan,
    InstancePlan,
    RangeCellPlan,
    SubnetPlan,
    _disk_type_self_link,
    _label_value,
    _machine_type_self_link,
)


def network_resource(plan: RangeCellPlan) -> ComputeResource:
    """Render a Compute Engine network insert body."""
    return {
        "name": plan["network"]["name"],
        "autoCreateSubnetworks": False,
        "routingConfig": {"routingMode": "REGIONAL"},
        "labels": plan["labels"],
    }


def subnetwork_resource(plan: RangeCellPlan, subnet: SubnetPlan) -> ComputeResource:
    """Render a Compute Engine subnetwork insert body."""
    return {
        "name": subnet["resource_name"],
        "network": subnet["network_link"],
        "ipCidrRange": subnet["cidr"],
        "region": plan["region"],
        "privateIpGoogleAccess": False,
        "labels": plan["labels"],
    }


def firewall_resource(plan: RangeCellPlan, firewall: FirewallPlan) -> ComputeResource:
    """Render a Compute Engine firewall insert body."""
    body: ComputeResource = {
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


def address_resource(plan: RangeCellPlan, instance: InstancePlan) -> ComputeResource:
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


def instance_resource(
    plan: RangeCellPlan,
    instance: InstancePlan,
    config: GCERangeCellConfig,
    *,
    ssh_public_key: str,
) -> ComputeResource:
    """Render a Compute Engine instance insert body."""
    profile = instance["profile"]
    body: ComputeResource = {
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
