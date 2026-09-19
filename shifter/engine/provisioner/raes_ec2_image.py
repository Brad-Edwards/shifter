"""Exact EC2 image policy for immutable RAES launch inputs.

Provider observations are required before creation; authored aliases and registry
rows cannot stand in for the AMI's actual platform, architecture or disk size.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from botocore.client import BaseClient
from shared.raes.artifact_binding import ArtifactBinding
from shared.raes.image_policy import (
    ResolvedImage,
    resolve_from_candidates,
    validate_management_ssh_port,
    validate_management_ssh_username,
)
from shared.runtime_plugin_binding import RuntimeTargetImageProfile

from raes_plan import RaesPlanNode

_AMI = re.compile(r"ami-(?:[0-9a-f]{8}|[0-9a-f]{17})")
_MACHINE = re.compile(r"[a-z][a-z0-9-]{1,30}\.[a-z0-9]{1,20}")


class Ec2ImageError(ValueError):
    """An image or size cannot be realized without substituting authored intent."""


@dataclass(frozen=True)
class Ec2ImageProfile:
    """Exact image and minimum sizing selected from the immutable operation input."""

    image_id: str
    instance_type: str
    disk_size_gb: int | None = None
    disk_type: str = "gp3"
    management_ssh_port: int = 22
    management_ssh_username: str = ""


@dataclass(frozen=True)
class VerifiedEc2Image:
    """Provider-observed source facts needed to create and verify a guest."""

    image_id: str
    instance_type: str
    root_device: str
    root_snapshot: str
    disk_size_gb: int
    disk_type: str
    architecture: str
    management_ssh_port: int
    management_ssh_username: str = ""


def resolve_ec2_image(
    node: RaesPlanNode,
    candidates: Sequence[dict[str, Any]],
    *,
    binding: ArtifactBinding | None = None,
    runtime_profile: RuntimeTargetImageProfile | None = None,
) -> Ec2ImageProfile:
    """Resolve a fenced artifact first, then exact registry pin, then concrete AMI."""
    resolved = _resolve_source(node, candidates, binding, runtime_profile)
    if not _AMI.fullmatch(resolved.image_ref):
        raise Ec2ImageError("EC2 realization requires an exact AMI ID")
    machine = resolved.machine_type or "m7i.large"
    if not _MACHINE.fullmatch(machine):
        raise Ec2ImageError("EC2 instance type is invalid")
    # The initial realization contract intentionally supports general-purpose
    # EBS only. IOPS/throughput-bearing volume types need their own typed intent.
    disk_type = resolved.disk_type or "gp3"
    if disk_type not in {"gp2", "gp3"}:
        raise Ec2ImageError("EC2 disk type is unsupported")
    if resolved.disk_size_gb is not None and (
        type(resolved.disk_size_gb) is not int or not 1 <= resolved.disk_size_gb <= 16384
    ):
        raise Ec2ImageError("EC2 disk size must be between 1 and 16384 GiB")
    return Ec2ImageProfile(
        resolved.image_ref,
        machine,
        resolved.disk_size_gb,
        disk_type,
        validate_management_ssh_port(resolved.management_ssh_port),
        validate_management_ssh_username(resolved.management_ssh_username),
    )


def _resolve_source(
    node: RaesPlanNode,
    candidates: Sequence[dict[str, Any]],
    binding: ArtifactBinding | None,
    runtime_profile: RuntimeTargetImageProfile | None,
) -> ResolvedImage:
    """Select the immutable artifact binding or exact authored registry candidate."""
    if binding is not None:
        if binding.target != node.address or (binding.image_id and binding.image_id != binding.image_ref):
            raise Ec2ImageError("EC2 artifact binding identity does not match the node")
        resolved = ResolvedImage(
            binding.image_ref,
            binding.machine_type or None,
            binding.disk_size_gb,
            binding.disk_type or None,
            binding.management_ssh_port,
            binding.management_ssh_username,
        )
    elif runtime_profile is not None:
        if runtime_profile.provider != "aws":
            raise Ec2ImageError("adapter image profile provider does not match EC2 realization")
        resolved = ResolvedImage(
            runtime_profile.image_ref,
            runtime_profile.machine_type or None,
            runtime_profile.disk_size_gb,
            runtime_profile.disk_type or None,
            runtime_profile.management_ssh_port,
            runtime_profile.management_ssh_username,
        )
    else:
        resolved = _registry_source(node, candidates)
    return resolved


def _registry_source(node: RaesPlanNode, candidates: Sequence[dict[str, Any]]) -> ResolvedImage:
    """Require a registry match or an explicitly authored concrete AMI."""
    resolved = resolve_from_candidates(candidates, version=node.image.version if node.image else None)
    if resolved is None and node.image and _AMI.fullmatch(node.image.name):
        resolved = ResolvedImage(node.image.name)
    if resolved is None:
        raise Ec2ImageError("No exact EC2 image mapping is registered for the authored source")
    return resolved


def _only(rows: object, label: str) -> dict[str, Any]:
    """Require exactly one structured provider observation."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise Ec2ImageError(f"EC2 {label} observation is unavailable or ambiguous")
    return rows[0]


def verify_ec2_image(node: RaesPlanNode, profile: Ec2ImageProfile, ec2: BaseClient) -> VerifiedEc2Image:
    """Verify image and type through authenticated EC2 reads before any mutation."""
    image = _only(ec2.describe_images(ImageIds=[profile.image_id]).get("Images"), "image")
    machine = _only(
        ec2.describe_instance_types(InstanceTypes=[profile.instance_type]).get("InstanceTypes"), "instance type"
    )
    if image.get("ImageId") != profile.image_id or image.get("State") != "available":
        raise Ec2ImageError("EC2 image identity is unavailable")
    if image.get("VirtualizationType") != "hvm" or image.get("RootDeviceType") != "ebs":
        raise Ec2ImageError("EC2 image must be an HVM guest with an EBS root disk")
    expected_windows = node.os_family == "windows"
    if node.os_family not in {"linux", "windows"} or (image.get("Platform") == "windows") != expected_windows:
        raise Ec2ImageError("EC2 image platform differs from the authored operating-system family")
    architecture = _verify_instance_type(node, profile, image, machine)
    root, snapshot, requested_size = _verify_root_disk(image, profile)
    return VerifiedEc2Image(
        profile.image_id,
        profile.instance_type,
        root,
        snapshot,
        requested_size,
        profile.disk_type,
        architecture,
        profile.management_ssh_port,
        profile.management_ssh_username,
    )


def _verify_instance_type(
    node: RaesPlanNode,
    profile: Ec2ImageProfile,
    image: dict[str, Any],
    machine: dict[str, Any],
) -> str:
    """Prove architecture compatibility and authored CPU and memory capacity."""
    architecture = image.get("Architecture")
    if (
        machine.get("InstanceType") != profile.instance_type
        or architecture not in {"x86_64", "arm64"}
        or architecture not in machine.get("ProcessorInfo", {}).get("SupportedArchitectures", [])
        or "hvm" not in machine.get("SupportedVirtualizationTypes", [])
    ):
        raise Ec2ImageError("EC2 instance type cannot realize the selected image architecture")
    for actual, required in (
        (machine.get("VCpuInfo", {}).get("DefaultVCpus"), node.vcpus or 1),
        (machine.get("MemoryInfo", {}).get("SizeInMiB"), node.ram_mib or 1),
    ):
        if type(actual) is not int or actual < required:
            raise Ec2ImageError("EC2 instance type is smaller than the authored resources")
    return architecture


def _verify_root_disk(image: dict[str, Any], profile: Ec2ImageProfile) -> tuple[str, str, int]:
    """Require a sole valid root snapshot and enough capacity for its image."""
    root = image.get("RootDeviceName")
    if not isinstance(root, str) or not re.fullmatch(r"/dev/[a-z][a-z0-9]{1,30}", root):
        raise Ec2ImageError("EC2 root device is invalid")
    # Refuse extra disks until their lifecycle and evidence are represented.
    disk = _only(image.get("BlockDeviceMappings"), "root disk")
    ebs = disk.get("Ebs", {})
    size, snapshot = ebs.get("VolumeSize"), ebs.get("SnapshotId")
    if (
        disk.get("DeviceName") != root
        or type(size) is not int
        or size < 1
        or not isinstance(snapshot, str)
        or not re.fullmatch(r"snap-(?:[0-9a-f]{8}|[0-9a-f]{17})", snapshot)
    ):
        raise Ec2ImageError("EC2 root snapshot observation is invalid")
    requested_size = profile.disk_size_gb if profile.disk_size_gb is not None else max(30, size)
    if requested_size < size:
        raise Ec2ImageError("EC2 boot volume cannot be smaller than the source snapshot")
    return root, snapshot, requested_size
