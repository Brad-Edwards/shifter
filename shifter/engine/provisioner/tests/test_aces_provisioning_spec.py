"""Tests for the provisioner-side ACES ProvisioningSpec reader (ADR-031).

The reader is a pure-stdlib mirror of the platform's locked Pydantic contract.
These tests drive it directly with dict payloads (the shape persisted in
``range_config``); the platform-side differential test guards drift against the
real ``shared.aces.provisioning_spec.ProvisioningSpec``.
"""

import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aces_provisioning_spec import (
    PROVISIONING_ONLY_PROFILE,
    PROVISIONING_SPEC_CONTRACT_VERSION,
    AcesProvisioningSpec,
    AcesProvisioningSpecError,
    parse,
)


def _valid_payload(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "profile": PROVISIONING_ONLY_PROFILE,
        "contract_version": PROVISIONING_SPEC_CONTRACT_VERSION,
        "nodes": [
            {
                "address": "attacker",
                "name": "attacker",
                "os_family": "Linux",
                "count": 2,
                "resources": {"ram_mib": 2048, "vcpus": 2},
                "image": {"name": "kali", "version": "2024.1"},
                "services": [{"name": "ssh", "port": 22, "protocol": "tcp"}],
                "network_addresses": ["net0"],
                "acls": [
                    {
                        "name": "allow-ssh",
                        "action": "ALLOW",
                        "direction": "ingress",
                        "protocol": "tcp",
                        "ports": [22],
                        "source": "internet",
                        "destination": "net0",
                    }
                ],
            }
        ],
        "networks": [
            {"address": "net0", "name": "default", "cidr": "10.0.0.0/24", "gateway": "10.0.0.1", "internal": False}
        ],
    }


class TestParseValid:
    def test_parses_a_full_spec(self):
        request_id = str(uuid4())
        spec = parse(_valid_payload(request_id))

        assert isinstance(spec, AcesProvisioningSpec)
        assert spec.request_id == request_id
        assert spec.profile == PROVISIONING_ONLY_PROFILE
        assert len(spec.nodes) == 1
        node = spec.nodes[0]
        assert node.os_family == "linux"  # normalized
        assert node.count == 2
        assert node.resources.ram_mib == 2048
        assert node.resources.vcpus == 2
        assert node.image is not None and node.image.name == "kali"
        assert node.services[0].port == 22
        assert node.network_addresses == ("net0",)
        assert node.acls[0].action == "allow"  # normalized
        assert node.acls[0].direction == "ingress"
        assert node.acls[0].ports == (22,)
        assert spec.networks[0].cidr == "10.0.0.0/24"
        assert spec.networks[0].internal is False

    def test_minimal_node_defaults(self):
        request_id = str(uuid4())
        payload = _valid_payload(request_id)
        payload["nodes"] = [{"address": "n", "name": "n", "os_family": "linux"}]
        spec = parse(payload)
        node = spec.nodes[0]
        assert node.count == 1
        assert node.resources.ram_mib is None
        assert node.image is None
        assert node.services == ()
        assert node.acls == ()


class TestSelfDiscrimination:
    def test_rejects_none(self):
        with pytest.raises(AcesProvisioningSpecError):
            parse(None)

    def test_rejects_wrong_contract_version(self):
        payload = _valid_payload(str(uuid4()))
        payload["contract_version"] = "provisioning-spec-v2"
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)

    def test_rejects_wrong_profile(self):
        payload = _valid_payload(str(uuid4()))
        payload["profile"] = "full"
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)

    def test_rejects_cyberscript_persisted_envelope(self):
        # A wrapped cyberscript RangeSpec envelope must not parse as an ACES spec.
        envelope = {"spec_schema": "range_spec", "spec_version": "1", "payload": {"scenario_id": "basic-attack"}}
        with pytest.raises(AcesProvisioningSpecError):
            parse(envelope)

    def test_rejects_non_uuid_request_id(self):
        payload = _valid_payload("not-a-uuid")
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)


class TestFieldValidation:
    def test_rejects_bad_acl_action(self):
        payload = _valid_payload(str(uuid4()))
        payload["nodes"][0]["acls"][0]["action"] = "reject"
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)

    def test_rejects_bad_acl_direction(self):
        payload = _valid_payload(str(uuid4()))
        payload["nodes"][0]["acls"][0]["direction"] = "sideways"
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)

    def test_rejects_missing_node_os_family(self):
        payload = _valid_payload(str(uuid4()))
        del payload["nodes"][0]["os_family"]
        with pytest.raises(AcesProvisioningSpecError):
            parse(payload)
