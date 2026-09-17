"""Reconstruct native EC2 cleanup from fenced ownership, independent of adapters.

Listing failures and incomplete pagination cannot establish absence. The caller
must supply the original creation generation from immutable operation evidence;
a newer teardown operation must never substitute its own generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID


class Ec2CleanupError(RuntimeError):
    """Value-free failure that retains ownership evidence for a cleanup retry."""


@dataclass(frozen=True)
class Ec2CleanupScope:
    environment: str
    region: str
    vpc_id: str
    request_id: UUID
    generation: UUID
    range_id: int

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.environment)
            or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", self.region)
            or not re.fullmatch(r"vpc-[0-9a-f]{8}(?:[0-9a-f]{9})?", self.vpc_id)
            or not isinstance(self.request_id, UUID)
            or not isinstance(self.generation, UUID)
            or type(self.range_id) is not int
            or self.range_id <= 0
        ):
            raise Ec2CleanupError("EC2 cleanup scope is invalid")

    def tags(self) -> dict[str, str]:
        return {
            "ManagedBy": "shifter",
            "shifter:system": "shifter",
            "shifter:environment": self.environment,
            "shifter:request_id": str(self.request_id),
            "shifter:range_id": str(self.range_id),
            "shifter:generation": str(self.generation),
        }


_LOOKUPS = {
    "instances": ("describe_instances", "Reservations", "InstanceId"),
    "volumes": ("describe_volumes", "Volumes", "VolumeId"),
    "interfaces": ("describe_network_interfaces", "NetworkInterfaces", "NetworkInterfaceId"),
    "groups": ("describe_security_groups", "SecurityGroups", "GroupId"),
    "route_tables": ("describe_route_tables", "RouteTables", "RouteTableId"),
    "subnets": ("describe_subnets", "Subnets", "SubnetId"),
}


def _inventory(scope: Ec2CleanupScope, ec2: Any) -> dict[str, list[dict[str, Any]]]:
    filters = [
        {"Name": f"tag:{key}", "Values": [value]} for key, value in scope.tags().items() if key != "shifter:generation"
    ]
    inventory = {}
    for category, (operation, key, identity) in _LOOKUPS.items():
        response = getattr(ec2, operation)(Filters=filters, MaxResults=1000)
        if response.get("NextToken"):
            raise Ec2CleanupError("EC2 cleanup inventory exceeded its bound")
        rows = response.get(key, [])
        if category == "instances":
            rows = [row for reservation in rows for row in reservation.get("Instances", [])]
            rows = [row for row in rows if row.get("State", {}).get("Name") != "terminated"]
        if not isinstance(rows, list) or len(rows) > 1000:
            raise Ec2CleanupError("EC2 cleanup inventory is malformed")
        ids = set()
        for row in rows:
            tags = {tag["Key"]: tag["Value"] for tag in row.get("Tags", [])}
            resource_id = row.get(identity)
            if (
                any(tags.get(key) != value for key, value in scope.tags().items())
                or (category != "volumes" and row.get("VpcId") != scope.vpc_id)
                or not isinstance(resource_id, str)
                or not resource_id
                or resource_id in ids
            ):
                raise Ec2CleanupError("EC2 cleanup found conflicting resource ownership")
            ids.add(resource_id)
        inventory[category] = rows
    _validate_references(inventory)
    return inventory


def _validate_references(inventory: dict[str, list[dict[str, Any]]]) -> None:
    subnets = {row["SubnetId"] for row in inventory["subnets"]}
    instances = {row["InstanceId"] for row in inventory["instances"]}
    for row in inventory["route_tables"]:
        for association in row.get("Associations", []):
            if (
                association.get("Main")
                or association.get("SubnetId") not in subnets
                or not association.get("RouteTableAssociationId")
            ):
                raise Ec2CleanupError("EC2 cleanup found a route association outside the range")
    if any(row.get("GroupName") == "default" for row in inventory["groups"]):
        raise Ec2CleanupError("EC2 cleanup cannot delete a platform default group")
    for row in inventory["volumes"]:
        if any(attachment.get("InstanceId") not in instances for attachment in row.get("Attachments", [])):
            raise Ec2CleanupError("EC2 cleanup found a volume attached outside the range")
    for row in inventory["interfaces"]:
        if row.get("RequesterManaged") or (
            row.get("Attachment") and row["Attachment"].get("InstanceId") not in instances
        ):
            raise Ec2CleanupError("EC2 cleanup found an interface managed outside the range")


def _result(scope: Ec2CleanupScope, inventory: dict[str, list[dict[str, Any]]], *, incomplete: bool = False):
    residuals = [{"category": key, "count": len(rows)} for key, rows in sorted(inventory.items()) if rows]
    return {
        "outcome": "INCOMPLETE" if incomplete else "RESIDUALS_FOUND" if residuals else "VERIFIED_ABSENT",
        "residual_categories": residuals,
        "scope": {"region": scope.region, "vpc": scope.vpc_id, "categories": list(_LOOKUPS)},
    }


def inventory_ec2_resources(scope: Ec2CleanupScope, ec2: Any) -> dict[str, Any]:
    """Return bounded inventory evidence; inability to observe never means absent."""
    try:
        return _result(scope, _inventory(scope, ec2))
    except Exception:
        return _result(scope, {}, incomplete=True)


def destroy_ec2_resources(scope: Ec2CleanupScope, ec2: Any) -> dict[str, Any]:
    """Preflight every resource, terminate guests, then remove owned dependencies."""
    try:
        inventory = _inventory(scope, ec2)
        ids = [row["InstanceId"] for row in inventory["instances"]]
        if ids:
            ec2.terminate_instances(InstanceIds=ids)
            ec2.get_waiter("instance_terminated").wait(InstanceIds=ids, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        # Instance termination usually removes the tagged boot disks and NICs.
        # Re-list before handling leftovers; never detach a foreign attachment.
        inventory = _inventory(scope, ec2)
        if inventory["instances"]:
            raise Ec2CleanupError("EC2 guests remain after termination")
        for row in inventory["interfaces"]:
            ec2.delete_network_interface(NetworkInterfaceId=row["NetworkInterfaceId"])
        for row in inventory["volumes"]:
            ec2.delete_volume(VolumeId=row["VolumeId"])
        for row in inventory["groups"]:
            ec2.delete_security_group(GroupId=row["GroupId"])
        for row in inventory["route_tables"]:
            for association in row.get("Associations", []):
                ec2.disassociate_route_table(AssociationId=association["RouteTableAssociationId"])
            ec2.delete_route_table(RouteTableId=row["RouteTableId"])
        for row in inventory["subnets"]:
            ec2.delete_subnet(SubnetId=row["SubnetId"])
        return inventory_ec2_resources(scope, ec2)
    except Ec2CleanupError:
        raise
    except Exception:
        raise Ec2CleanupError("EC2 resource cleanup failed; ownership evidence must be retained") from None
