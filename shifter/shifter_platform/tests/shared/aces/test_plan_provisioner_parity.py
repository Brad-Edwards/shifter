"""Drift guard: the provisioner plan reader extracts what the reference backend does.

Per ADR-032 Shifter rides the ACES contract: the platform serializes the compiled
ProvisioningPlan (``serialize_provisioning_plan``) and the provisioner reads it via
accessors that mirror the reference ACES backend ``aces_backend_libvirt``. This
test is the differential oracle: it serializes a real plan, parses it with the
provisioner reader (``shifter/engine/provisioner/aces_plan.py``, loaded standalone
since it is pure-stdlib), and asserts the extracted image / memory / vcpus /
os_family match ``aces_backend_libvirt``'s own accessors for the same payloads.
If the ACES payload convention shifts upstream, this fails until the provisioner
reader is realigned.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from aces_backend_libvirt import realization as libvirt_realization
from aces_backend_libvirt._payload import _os_family as libvirt_os_family
from aces_contracts.planning import PlannedResource, ProvisioningPlan, RuntimeDomain

from shared.aces.runtime_target import serialize_provisioning_plan

_PROVISIONER_READER = Path(__file__).resolve().parents[4] / "engine" / "provisioner" / "aces_plan.py"


def _load_provisioner_reader():
    # aces_plan imports its sibling aces_composition, so the provisioner dir must be
    # importable when the reader is loaded standalone.
    provisioner_dir = str(_PROVISIONER_READER.parent)
    if provisioner_dir not in sys.path:
        sys.path.insert(0, provisioner_dir)
    spec = importlib.util.spec_from_file_location("aces_plan_provisioner", _PROVISIONER_READER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reader():
    return _load_provisioner_reader()


def _node_payload() -> dict:
    return {
        "name": "web",
        "os_family": "linux",
        "count": 3,
        "spec": {
            "node": {"source": {"name": "ubuntu-22.04", "version": "1.2"}, "resources": {"ram": 2147483648, "cpu": 4}},
            "infrastructure": {"networks": ["net.lan"]},
        },
    }


def _plan() -> ProvisioningPlan:
    node = PlannedResource(
        address="node.web", domain=RuntimeDomain.PROVISIONING, resource_type="node", payload=_node_payload()
    )
    network = PlannedResource(
        address="net.lan",
        domain=RuntimeDomain.PROVISIONING,
        resource_type="network",
        payload={"name": "lan", "spec": {"infrastructure": {"properties": {"cidr": "10.9.0.0/24"}}}},
    )
    return ProvisioningPlan(resources={node.address: node, network.address: network})


class TestProvisionerReaderParity:
    def test_kind_constant_matches_serializer(self, reader):
        from shared.aces.runtime_target import ACES_PROVISIONING_PLAN_KIND

        assert reader.ACES_PROVISIONING_PLAN_KIND == ACES_PROVISIONING_PLAN_KIND

    def test_extraction_matches_reference_backend(self, reader):
        serialized = serialize_provisioning_plan(_plan())
        parsed = reader.parse_plan(serialized)

        node = next(n for n in parsed.nodes if n.address == "node.web")
        payload = _node_payload()

        # Image name matches aces_backend_libvirt._image_ref (source name verbatim).
        assert node.image is not None
        assert node.image.name == libvirt_realization._image_ref(payload)
        assert node.image.version == "1.2"
        # Sizing matches the reference conversions for present values.
        assert node.ram_mib == libvirt_realization._memory_mib(payload["spec"]["node"]["resources"]["ram"])
        assert node.vcpus == libvirt_realization._vcpus(payload["spec"]["node"]["resources"]["cpu"])
        # os_family matches the reference accessor.
        assert node.os_family == libvirt_os_family(payload)
        # Network membership resolved to the declared network address.
        assert node.network_addresses == ("net.lan",)

        network = next(n for n in parsed.networks if n.address == "net.lan")
        assert network.cidr == "10.9.0.0/24"

    def test_acl_extraction_matches_reference_backend(self, reader):
        from aces_backend_libvirt.acls import realize_node_acls

        raw_acls = [
            {
                "name": "ssh",
                "action": "allow",
                "direction": "in",
                "protocol": "tcp",
                "ports": [22],
                "from_net": "net.lan",
            }
        ]
        node = PlannedResource(
            address="node.web",
            domain=RuntimeDomain.PROVISIONING,
            resource_type="node",
            payload={"name": "web", "os_family": "linux", "spec": {"infrastructure": {"acls": raw_acls}}},
        )
        network = PlannedResource(
            address="net.lan",
            domain=RuntimeDomain.PROVISIONING,
            resource_type="network",
            payload={"name": "lan", "spec": {"infrastructure": {"properties": {"cidr": "10.9.0.0/24"}}}},
        )
        serialized = serialize_provisioning_plan(
            ProvisioningPlan(resources={node.address: node, network.address: network})
        )
        parsed_acl = reader.parse_plan(serialized).nodes[0].acls[0]

        cidr_lookup = {"net.lan": "10.9.0.0/24", "lan": "10.9.0.0/24"}
        ref_acls, ref_diags = realize_node_acls(node, raw_acls, cidr_lookup)
        assert not ref_diags
        ref = ref_acls[0]
        # Normalized fields match the reference; endpoints resolve to the same CIDR.
        assert (parsed_acl.action, parsed_acl.direction, parsed_acl.protocol, parsed_acl.ports) == (
            ref.action,
            ref.direction,
            ref.protocol,
            ref.ports,
        )
        assert cidr_lookup.get(parsed_acl.from_net) == ref.src_cidr
        assert (cidr_lookup.get(parsed_acl.to_net) if parsed_acl.to_net else None) == ref.dst_cidr
