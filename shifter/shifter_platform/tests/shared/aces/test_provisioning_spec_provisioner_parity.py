"""Drift guard: the provisioner-side reader accepts the platform contract's output.

The provisioner (a separate deployable) cannot import the platform's Pydantic
``shared.aces.provisioning_spec`` contract, so it ships a pure-stdlib mirror at
``shifter/engine/provisioner/aces_provisioning_spec.py`` (ADR-031). This test is
the differential oracle that keeps the two representations from drifting: it
serializes a real :class:`ProvisioningSpec` and asserts the provisioner reader
parses it back field-for-field. The provisioner module is pure-stdlib, so it can
be loaded standalone by file path without provisioner path setup.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from shared.aces.contracts import SHIFTER_BACKEND_PROFILE
from shared.aces.provisioning_spec import (
    PROVISIONING_SPEC_CONTRACT_VERSION,
    ProvisioningAclRule,
    ProvisioningImage,
    ProvisioningNetworkSpec,
    ProvisioningNodeSpec,
    ProvisioningResources,
    ProvisioningService,
    ProvisioningSpec,
)

_PROVISIONER_READER = Path(__file__).resolve().parents[4] / "engine" / "provisioner" / "aces_provisioning_spec.py"


def _load_provisioner_reader():
    spec = importlib.util.spec_from_file_location("aces_provisioning_spec_provisioner", _PROVISIONER_READER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve this module's string
    # annotations (``from __future__ import annotations``) during class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reader():
    return _load_provisioner_reader()


def _sample_spec() -> ProvisioningSpec:
    return ProvisioningSpec(
        request_id="11111111-1111-1111-1111-111111111111",
        nodes=(
            ProvisioningNodeSpec(
                address="attacker",
                name="attacker",
                os_family="linux",
                count=2,
                resources=ProvisioningResources(ram_mib=2048, vcpus=2),
                image=ProvisioningImage(name="kali", version="2024.1"),
                services=(ProvisioningService(name="ssh", port=22, protocol="tcp"),),
                network_addresses=("net0",),
                acls=(
                    ProvisioningAclRule(
                        name="allow-ssh",
                        action="allow",
                        direction="ingress",
                        protocol="tcp",
                        ports=(22,),
                        source="internet",
                        destination="net0",
                    ),
                ),
            ),
        ),
        networks=(ProvisioningNetworkSpec(address="net0", name="default", cidr="10.0.0.0/24", gateway="10.0.0.1"),),
    )


class TestProvisionerReaderParity:
    def test_constants_match_platform_contract(self, reader):
        assert reader.PROVISIONING_SPEC_CONTRACT_VERSION == PROVISIONING_SPEC_CONTRACT_VERSION
        assert reader.PROVISIONING_ONLY_PROFILE == SHIFTER_BACKEND_PROFILE

    def test_reader_parses_serialized_platform_spec(self, reader):
        spec = _sample_spec()
        parsed = reader.parse(spec.model_dump(mode="json"))

        assert parsed.request_id == spec.request_id
        assert parsed.profile == spec.profile
        assert parsed.contract_version == spec.contract_version
        assert len(parsed.nodes) == len(spec.nodes)

        node, src = parsed.nodes[0], spec.nodes[0]
        assert node.address == src.address
        assert node.os_family == src.os_family
        assert node.count == src.count
        assert node.resources.ram_mib == src.resources.ram_mib
        assert node.resources.vcpus == src.resources.vcpus
        assert node.image is not None and node.image.name == src.image.name
        assert node.image.version == src.image.version
        assert node.services[0].port == src.services[0].port
        assert node.network_addresses == tuple(src.network_addresses)
        assert node.acls[0].action == src.acls[0].action
        assert node.acls[0].direction == src.acls[0].direction
        assert node.acls[0].ports == tuple(src.acls[0].ports)
        assert node.acls[0].source == src.acls[0].source
        assert node.acls[0].destination == src.acls[0].destination

        net, net_src = parsed.networks[0], spec.networks[0]
        assert net.address == net_src.address
        assert net.cidr == net_src.cidr
        assert net.gateway == net_src.gateway
        assert net.internal == net_src.internal
