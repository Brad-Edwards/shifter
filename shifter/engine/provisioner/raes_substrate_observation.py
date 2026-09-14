"""Read VM existence from GCE rather than inferring it from the authored type."""

from __future__ import annotations

import re
from typing import Any


def verify_prepared_source(plan, instance, clients):
    """Reject a recreated image name before installing any range credentials."""
    profile = instance["profile"]
    if not profile.source_image_id:
        return
    prefix = f"projects/{plan['project_id']}/global/images/"
    name = profile.source_image.removeprefix(prefix)
    if not profile.source_image.startswith(prefix) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("prepared source is outside the range project")
    image = clients.images.get(project=plan["project_id"], image=name)
    if str(image.id) != profile.source_image_id or image.status != "READY":
        raise ValueError("prepared source identity is unavailable")


def observe_gce_substrates(plan: dict[str, Any], clients: Any) -> list[dict[str, str]]:
    """Read each created instance through the authenticated provider client."""
    observations = []
    for instance in plan["instances"]:
        actual = clients.instances.get(
            project=plan["project_id"], zone=plan["zone"], instance=instance["resource_name"]
        )
        if actual is None or actual.name != instance["resource_name"] or not actual.id or not actual.machine_type:
            raise ValueError("compute-substrate observation is unavailable or invalid")
        expected_image_id = getattr(instance.get("profile"), "source_image_id", "")
        if expected_image_id:
            _verify_boot_image(plan, actual, clients, expected_image_id)
        observations.append({"instance_key": instance["uuid"], "value": "virtual-machine"})
    return observations


def _verify_boot_image(plan, guest, clients, expected):
    """An image name can be recreated; its actual boot disk must retain the admitted ID."""
    boot = [disk for disk in guest.disks if disk.boot]
    if len(boot) != 1:
        raise ValueError("prepared image boot disk is ambiguous")
    source = (
        boot[0]
        .source.removeprefix("https://www.googleapis.com/compute/v1/")
        .removeprefix("https://compute.googleapis.com/compute/v1/")
    )
    prefix = f"projects/{plan['project_id']}/zones/{plan['zone']}/disks/"
    name = source.removeprefix(prefix)
    if not source.startswith(prefix) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("prepared image boot disk is outside the range scope")
    disk = clients.disks.get(project=plan["project_id"], zone=plan["zone"], disk=name)
    if str(disk.source_image_id) != expected:
        raise ValueError("prepared image provider identity changed")
