"""Oracle battery for the locked ACES-native ProvisioningSpec contract (ADR-031).

Contract-locked development (ADR-087): every ``ACESPS-*`` invariant declared in
:data:`shared.aces.provisioning_spec.ACESPS_INVARIANTS` must have at least one
negative case here that proves the validator rejects a violation, plus
round-trip / normalization / structural property tests. The
``test_every_invariant_has_a_negative_case`` meta-test is the invariant-to-check
inventory: it fails if an invariant is added without an enforcing test.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from shared.aces.provisioning_spec import (
    ACESPS_INVARIANTS,
    PROVISIONING_SPEC_CONTRACT_VERSION,
    ProvisioningImage,
    ProvisioningService,
    ProvisioningSpecError,
    provisioning_spec_json_schema,
    validate_provisioning_spec,
)

REQUEST_ID = "12345678-1234-5678-1234-567812345678"


def _valid_payload() -> dict:
    """A fully-populated, valid spec payload (JSON-shaped)."""
    return {
        "request_id": REQUEST_ID,
        "nodes": [
            {
                "address": "provision.node.web",
                "name": "web",
                "os_family": "Linux",
                "count": 2,
                "resources": {"ram_mib": 2048, "vcpus": 2},
                "image": {"name": "ubuntu-22.04", "version": "1.0.0"},
                "services": [{"name": "ssh", "port": 22, "protocol": "tcp"}],
                "network_addresses": ["provision.network.lan"],
            },
            {
                "address": "provision.node.dc",
                "name": "dc",
                "os_family": "windows",
                "network_addresses": ["provision.network.lan"],
            },
        ],
        "networks": [
            {
                "address": "provision.network.lan",
                "name": "lan",
                "cidr": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "internal": False,
                "acls": [
                    {
                        "action": "ALLOW",
                        "direction": "ingress",
                        "protocol": "tcp",
                        "ports": [22, 3389],
                        "source": "internet",
                        "destination": "provision.network.lan",
                    }
                ],
            }
        ],
    }


def test_valid_spec_constructs_and_normalizes() -> None:
    spec = validate_provisioning_spec(_valid_payload())
    assert spec.profile == "provisioning-only"
    assert spec.contract_version == PROVISIONING_SPEC_CONTRACT_VERSION
    assert spec.request_id == REQUEST_ID
    assert [node.os_family for node in spec.nodes] == ["linux", "windows"]  # ACESPS-010 lowercased
    assert spec.networks[0].acls[0].action == "allow"  # normalized


def test_json_round_trip_is_lossless() -> None:
    spec = validate_provisioning_spec(_valid_payload())
    again = validate_provisioning_spec(spec.model_dump(mode="json"))
    assert again == spec


@pytest.mark.parametrize("_iteration", range(25))
def test_round_trip_property_over_variants(_iteration: int) -> None:
    """Deterministic generative round-trip: mutate the valid payload and assert
    every accepted spec survives a JSON round-trip unchanged."""
    payload = _valid_payload()
    # Deterministic, seed-free variation by iteration index.
    payload["nodes"][0]["count"] = _iteration + 1
    payload["nodes"][0]["resources"] = {"ram_mib": 512 * (_iteration + 1), "vcpus": (_iteration % 4) + 1}
    if _iteration % 2:
        payload["nodes"][0].pop("image")
    if _iteration % 3 == 0:
        payload["networks"][0]["internal"] = True
    spec = validate_provisioning_spec(payload)
    assert validate_provisioning_spec(spec.model_dump(mode="json")) == spec


def test_unknown_keys_rejected() -> None:
    payload = _valid_payload()
    payload["surprise"] = "value"
    with pytest.raises(ProvisioningSpecError):
        validate_provisioning_spec(payload)
    node_payload = _valid_payload()
    node_payload["nodes"][0]["role"] = "attacker"  # cyberscript concept must not exist
    with pytest.raises(ProvisioningSpecError):
        validate_provisioning_spec(node_payload)


# --- Negative cases: one per ACESPS-* invariant (the check inventory) ---------


def _with(**over) -> dict:
    payload = _valid_payload()
    payload.update(copy.deepcopy(over))
    return payload


NEGATIVE_CASES: dict[str, dict] = {
    "ACESPS-001": _with(request_id="not-a-uuid"),
    "ACESPS-002": _with(profile="full-remote-control-plane"),
    "ACESPS-003": _with(contract_version="provisioning-spec-v2"),
    "ACESPS-004": _with(
        nodes=[
            {"address": "dup", "name": "a", "os_family": "linux"},
            {"address": "dup", "name": "b", "os_family": "linux"},
        ],
        networks=[],
    ),
    "ACESPS-005": _with(nodes=[{"address": "n", "name": "n", "os_family": "linux", "count": 0}], networks=[]),
    "ACESPS-006": _with(
        nodes=[{"address": "n", "name": "n", "os_family": "linux", "resources": {"ram_mib": 0}}], networks=[]
    ),
    "ACESPS-007": _with(
        nodes=[{"address": "n", "name": "n", "os_family": "linux", "network_addresses": ["provision.network.missing"]}],
        networks=[],
    ),
    "ACESPS-008": _with(
        nodes=[],
        networks=[
            {
                "address": "provision.network.a",
                "name": "a",
                "acls": [
                    {
                        "action": "allow",
                        "direction": "ingress",
                        "source": "provision.network.b",
                        "destination": "provision.network.a",
                    }
                ],
            }
        ],
    ),
    "ACESPS-009": _with(
        nodes=[{"address": "n", "name": "n", "os_family": "linux", "image": {"name": "image-with-password-baked-in"}}],
        networks=[],
    ),
    "ACESPS-010": _with(nodes=[{"address": "n", "name": "n", "os_family": "  "}], networks=[]),
    "ACESPS-011": _with(
        nodes=[{"address": "n", "name": "n", "os_family": "linux", "services": [{"name": "x", "port": 70000}]}],
        networks=[],
    ),
}


@pytest.mark.parametrize("invariant", sorted(NEGATIVE_CASES))
def test_invariant_violation_rejected(invariant: str) -> None:
    with pytest.raises(ProvisioningSpecError):
        validate_provisioning_spec(NEGATIVE_CASES[invariant])


def test_every_invariant_has_a_negative_case() -> None:
    """Invariant-to-check inventory: no ACESPS-* id without an enforcing test."""
    assert set(ACESPS_INVARIANTS) == set(NEGATIVE_CASES), (
        "Every ACESPS-* invariant must have exactly one negative case; "
        f"uncovered={set(ACESPS_INVARIANTS) - set(NEGATIVE_CASES)}, "
        f"stale={set(NEGATIVE_CASES) - set(ACESPS_INVARIANTS)}"
    )


def test_custom_invariant_messages_carry_their_id() -> None:
    """Custom (non-pure-Pydantic-constraint) invariants surface their id."""
    for invariant in ("ACESPS-001", "ACESPS-002", "ACESPS-003", "ACESPS-004", "ACESPS-007", "ACESPS-008"):
        with pytest.raises(ProvisioningSpecError) as excinfo:
            validate_provisioning_spec(NEGATIVE_CASES[invariant])
        assert invariant in str(excinfo.value)


def test_direct_construction_rejects_secret_markers() -> None:
    # Direct model construction surfaces the invariant as a Pydantic
    # ValidationError wrapping ProvisioningSpecError (the message keeps ACESPS-009).
    for bad in ("api_key=abc", "my-token-1234", "svc-secret"):
        with pytest.raises(ValidationError):
            ProvisioningImage(name=bad)
    with pytest.raises(ValidationError):
        ProvisioningService(name="password-svc", port=443)


def test_json_schema_is_generated_and_forbids_extra() -> None:
    schema = provisioning_spec_json_schema()
    assert schema["title"] == "ProvisioningSpec"
    assert schema.get("additionalProperties") is False
    assert set(schema["properties"]) >= {"request_id", "profile", "contract_version", "nodes", "networks"}
