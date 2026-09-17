"""Exact image selection and independent EC2 image/type preflight."""

from unittest.mock import Mock

import pytest

from raes_ec2_image import Ec2ImageError, resolve_ec2_image, verify_ec2_image
from raes_plan import RaesPlanImage, RaesPlanNode


def node(**extra):
    return RaesPlanNode(
        address="node.host",
        name="host",
        os_family="linux",
        count=1,
        network_addresses=(),
        image=RaesPlanImage(name="container-host", version="1"),
        **extra,
    )


def candidate(**extra):
    return {
        "source_version": "1",
        "image_ref": "ami-0123456789abcdef0",
        "machine_type": "m7i.large",
        "management_ssh_port": 2222,
        **extra,
    }


def client():
    ec2 = Mock()
    ec2.describe_images.return_value = {
        "Images": [
            {
                "ImageId": "ami-0123456789abcdef0",
                "State": "available",
                "Architecture": "x86_64",
                "VirtualizationType": "hvm",
                "RootDeviceType": "ebs",
                "RootDeviceName": "/dev/sda1",
                "BlockDeviceMappings": [
                    {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 30, "SnapshotId": "snap-0123456789abcdef0"}}
                ],
            }
        ]
    }
    ec2.describe_instance_types.return_value = {
        "InstanceTypes": [
            {
                "InstanceType": "m7i.large",
                "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "VCpuInfo": {"DefaultVCpus": 2},
                "MemoryInfo": {"SizeInMiB": 8192},
                "SupportedVirtualizationTypes": ["hvm"],
            }
        ]
    }
    return ec2


def test_pinned_registry_image_keeps_management_transport_and_source_identity():
    profile = resolve_ec2_image(node(), [candidate()])
    assert profile.image_id == "ami-0123456789abcdef0"
    assert profile.management_ssh_port == 2222
    actual = verify_ec2_image(node(ram_mib=4096, vcpus=2), profile, client())
    assert actual.root_device == "/dev/sda1"
    assert actual.disk_size_gb >= 30
    assert actual.image_id == profile.image_id


@pytest.mark.parametrize(
    "image_ref", ["ami-latest", "ami-0123456789abcdef0 trailing", "ssm:/latest", "projects/x/global/images/host"]
)
def test_registry_cannot_substitute_mutable_or_other_provider_image(image_ref):
    with pytest.raises(Ec2ImageError):
        resolve_ec2_image(node(), [candidate(image_ref=image_ref)])


def test_missing_pin_never_falls_back_to_any_version():
    with pytest.raises(Ec2ImageError):
        resolve_ec2_image(node(), [candidate(source_version="")])


@pytest.mark.parametrize(
    "alter",
    [
        lambda ec2: ec2.describe_images.return_value["Images"][0].update(State="pending"),
        lambda ec2: ec2.describe_images.return_value["Images"][0].update(ImageId="ami-fffffffffffffffff"),
        lambda ec2: ec2.describe_images.return_value["Images"][0].update(Platform="windows"),
        lambda ec2: ec2.describe_images.return_value["Images"][0].update(Architecture="arm64"),
        lambda ec2: ec2.describe_instance_types.return_value["InstanceTypes"][0]["MemoryInfo"].update(SizeInMiB=1024),
        lambda ec2: ec2.describe_instance_types.return_value["InstanceTypes"][0]["VCpuInfo"].update(DefaultVCpus=1),
    ],
)
def test_provider_observation_must_satisfy_requested_image_and_resources(alter):
    ec2 = client()
    alter(ec2)
    with pytest.raises(Ec2ImageError):
        verify_ec2_image(node(ram_mib=4096, vcpus=2), resolve_ec2_image(node(), [candidate()]), ec2)
    assert not ec2.run_instances.called


def test_explicit_disk_cannot_shrink_the_source_snapshot():
    with pytest.raises(Ec2ImageError):
        verify_ec2_image(node(), resolve_ec2_image(node(), [candidate(disk_size_gb=20)]), client())
