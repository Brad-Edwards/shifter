"""ACES-native, cyberscript-free provisioning specification (LOCKED contract).

This module is the ADR-031 locked contract seam between the ACES half of the
ACES-native provisioning path and the realization half (engine + provisioner).

* The ACES half (``shared.aces.runtime_target``, which may import the ``aces-sdl``
  tooling) interprets a compiled ACES ``ProvisioningPlan`` into a
  :class:`ProvisioningSpec`.
* The realization half (``cms``/``engine``/provisioner) consumes a
  :class:`ProvisioningSpec` **without importing any ``aces_*`` package**
  (ADR-024). This module therefore imports only stdlib, Pydantic, and the
  runtime-safe :mod:`shared.aces.contracts` constants.

The spec is a *neutral* projection of a provisioning-only topology: compute
nodes (os family, count, cpu/memory resources, image reference, services,
network membership) and networks (cidr, gateway, isolation, ACL rules). It
contains **no** cyberscript concepts: no ``scenario_id``, no ``role`` enum, no
``os_type`` enum. Backend-owned realization detail (concrete image ids, machine
sizes, subnet allocation, provider configuration, secrets) is deliberately
absent — that is resolved by the provisioner, not carried as authored ACES
semantics (ADR-031-R4).

Contract-locked development (ADR-087): every invariant below carries a stable
``ACESPS-*`` id. The enforcing check for each is a Pydantic validator on these
models; the negative/property battery in
``tests/shared/aces/test_provisioning_spec.py`` is the inventory that proves the
validators actually reject violations. :data:`ACESPS_INVARIANTS` is the
machine-readable id -> description inventory.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.aces.contracts import SHIFTER_BACKEND_PROFILE

__all__ = [
    "ACESPS_INVARIANTS",
    "ACL_RESERVED_ENDPOINTS",
    "PROVISIONING_SPEC_CONTRACT_VERSION",
    "ProvisioningAclRule",
    "ProvisioningImage",
    "ProvisioningNetworkSpec",
    "ProvisioningNodeSpec",
    "ProvisioningResources",
    "ProvisioningService",
    "ProvisioningSpec",
    "ProvisioningSpecError",
    "provisioning_spec_json_schema",
    "validate_provisioning_spec",
]

#: The single supported version of this contract. Bumping it is an
#: architecture event (ADR-031-R3): design-authority approval + ADR touch.
PROVISIONING_SPEC_CONTRACT_VERSION = "provisioning-spec-v1"

#: ACL endpoint tokens that are not network addresses. ``any`` is unrestricted,
#: ``internet`` is the public egress edge, ``host`` is the range's own host.
ACL_RESERVED_ENDPOINTS = frozenset({"any", "internet", "host"})

#: Stable invariant inventory (ADR-087). Each id is enforced by a validator on
#: the models in this module and exercised by the negative battery.
ACESPS_INVARIANTS: dict[str, str] = {
    "ACESPS-001": "request_id is a canonical UUID string.",
    "ACESPS-002": "profile equals the Shifter backend profile (provisioning-only).",
    "ACESPS-003": "contract_version equals the single supported spec version.",
    "ACESPS-004": "node and network addresses are non-empty, single-line, and unique within their kind.",
    "ACESPS-005": "node.count is an integer >= 1.",
    "ACESPS-006": "resources.ram_mib and resources.vcpus, when present, are integers >= 1.",
    "ACESPS-007": "every node.network_addresses entry references a declared network address.",
    "ACESPS-008": "every ACL source/destination references a declared network address or a reserved endpoint token.",
    "ACESPS-009": "string fields are single-line and free of secret markers / control characters.",
    "ACESPS-010": "node.os_family is a non-empty single-line token.",
    "ACESPS-011": "service.port and ACL ports are within 1..65535.",
}

_MAX_STRING = 512
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Secret-marker substrings rejected in authored string fields (ACESPS-009).
# Realization secrets are backend-owned and must never travel in the spec.
_SECRET_MARKERS = (
    "secret",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "-----begin",
)


class ProvisioningSpecError(ValueError):
    """Raised when a ProvisioningSpec violates an ACESPS-* invariant."""


def _clean_token(value: str, *, field: str, invariant: str) -> str:
    """Validate a single-line, non-empty, secret-free token (ACESPS-004/009/010)."""
    if not isinstance(value, str):
        raise ProvisioningSpecError(f"{invariant}: {field} must be a string")
    text = value.strip()
    if not text:
        raise ProvisioningSpecError(f"{invariant}: {field} must be non-empty")
    if len(text) > _MAX_STRING or "\n" in value or _CONTROL_CHARS.search(value):
        raise ProvisioningSpecError(f"{invariant}: {field} must be a single bounded line")
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ProvisioningSpecError(f"ACESPS-009: {field} must not carry secret material")
    return text


_Port = Annotated[int, Field(ge=1, le=65535)]


class ProvisioningResources(BaseModel):
    """Provider-neutral sizing hint for a node (ACESPS-006).

    ``ram_mib`` and ``vcpus`` are portable sizing values derived by the backend
    from the ACES node ``resources`` (ram bytes -> MiB, cpu cores -> vcpus).
    Both are optional: an absent value means "let the backend choose its
    os_family default".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ram_mib: int | None = Field(default=None, ge=1)
    vcpus: int | None = Field(default=None, ge=1)


class ProvisioningImage(BaseModel):
    """Provider-neutral image reference (ACES ``source``).

    ``name``/``version`` are the *authored* image identity (e.g. an OVA/template
    name), not a resolved cloud image id. The provisioner maps this plus
    ``os_family`` to a concrete backend image (ADR-031-R4).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_token(value, field="image.name", invariant="ACESPS-009")

    @field_validator("version")
    @classmethod
    def _version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_token(value, field="image.version", invariant="ACESPS-009")


class ProvisioningService(BaseModel):
    """A listening service on a node (name + port + protocol) (ACESPS-011)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    port: _Port
    protocol: str = "tcp"

    @field_validator("name", "protocol")
    @classmethod
    def _tokens(cls, value: str) -> str:
        return _clean_token(value, field="service field", invariant="ACESPS-009")


class ProvisioningAclRule(BaseModel):
    """A directional allow/deny rule on a node between network endpoints.

    ACES authors ACLs on node infrastructure (direction in/out/inout, from_net/
    to_net network refs). This is the neutral, node-scoped projection:
    ``source``/``destination`` reference a declared network address or a reserved
    endpoint token (:data:`ACL_RESERVED_ENDPOINTS`) (ACESPS-008/011).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    action: str
    direction: str
    protocol: str = "any"
    ports: tuple[_Port, ...] = ()
    source: str
    destination: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_token(value, field="acl.name", invariant="ACESPS-009")

    @field_validator("action")
    @classmethod
    def _action(cls, value: str) -> str:
        token = _clean_token(value, field="acl.action", invariant="ACESPS-009").lower()
        if token not in {"allow", "deny"}:
            raise ProvisioningSpecError("ACESPS-008: acl.action must be 'allow' or 'deny'")
        return token

    @field_validator("direction")
    @classmethod
    def _direction(cls, value: str) -> str:
        token = _clean_token(value, field="acl.direction", invariant="ACESPS-009").lower()
        if token not in {"ingress", "egress", "both"}:
            raise ProvisioningSpecError("ACESPS-008: acl.direction must be 'ingress', 'egress', or 'both'")
        return token

    @field_validator("protocol", "source", "destination")
    @classmethod
    def _endpoints(cls, value: str) -> str:
        return _clean_token(value, field="acl endpoint", invariant="ACESPS-009")


class ProvisioningNodeSpec(BaseModel):
    """A compute node (VM) to provision (ACESPS-004/005/006/010)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    name: str
    os_family: str
    count: int = Field(default=1, ge=1)
    resources: ProvisioningResources = Field(default_factory=ProvisioningResources)
    image: ProvisioningImage | None = None
    services: tuple[ProvisioningService, ...] = ()
    network_addresses: tuple[str, ...] = ()
    acls: tuple[ProvisioningAclRule, ...] = ()

    @field_validator("address", "name")
    @classmethod
    def _identity(cls, value: str) -> str:
        return _clean_token(value, field="node identity", invariant="ACESPS-004")

    @field_validator("os_family")
    @classmethod
    def _os_family(cls, value: str) -> str:
        return _clean_token(value, field="node.os_family", invariant="ACESPS-010").lower()

    @field_validator("network_addresses")
    @classmethod
    def _networks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_clean_token(item, field="node.network_addresses[]", invariant="ACESPS-004") for item in value)


class ProvisioningNetworkSpec(BaseModel):
    """A network the range's nodes attach to (ACESPS-004)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    name: str
    cidr: str | None = None
    gateway: str | None = None
    internal: bool = False

    @field_validator("address", "name")
    @classmethod
    def _identity(cls, value: str) -> str:
        return _clean_token(value, field="network identity", invariant="ACESPS-004")

    @field_validator("cidr", "gateway")
    @classmethod
    def _addressing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_token(value, field="network addressing", invariant="ACESPS-009")


class ProvisioningSpec(BaseModel):
    """The ACES-native, cyberscript-free provisioning specification (ADR-031).

    This is the sole persisted artifact the engine/provisioner consume for the
    ACES-native path. Persist via :meth:`model_dump` (mode="json") and re-load
    via :meth:`model_validate`; both round-trip losslessly (ACESPS property
    battery).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    profile: str = SHIFTER_BACKEND_PROFILE
    contract_version: str = PROVISIONING_SPEC_CONTRACT_VERSION
    nodes: tuple[ProvisioningNodeSpec, ...] = ()
    networks: tuple[ProvisioningNetworkSpec, ...] = ()

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        try:
            return str(UUID(str(value)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ProvisioningSpecError("ACESPS-001: request_id must be a canonical UUID string") from exc

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        if value != SHIFTER_BACKEND_PROFILE:
            raise ProvisioningSpecError(f"ACESPS-002: profile must be {SHIFTER_BACKEND_PROFILE!r}")
        return value

    @field_validator("contract_version")
    @classmethod
    def _contract_version(cls, value: str) -> str:
        if value != PROVISIONING_SPEC_CONTRACT_VERSION:
            raise ProvisioningSpecError(f"ACESPS-003: contract_version must be {PROVISIONING_SPEC_CONTRACT_VERSION!r}")
        return value

    @model_validator(mode="after")
    def _cross_references(self) -> ProvisioningSpec:
        node_addresses = [node.address for node in self.nodes]
        network_addresses = [network.address for network in self.networks]

        if len(set(node_addresses)) != len(node_addresses):
            raise ProvisioningSpecError("ACESPS-004: duplicate node address")
        if len(set(network_addresses)) != len(network_addresses):
            raise ProvisioningSpecError("ACESPS-004: duplicate network address")

        declared_networks = set(network_addresses)
        for node in self.nodes:
            for ref in node.network_addresses:
                if ref not in declared_networks:
                    raise ProvisioningSpecError(
                        f"ACESPS-007: node {node.address!r} references undeclared network {ref!r}"
                    )

        acl_targets = declared_networks | ACL_RESERVED_ENDPOINTS
        for node in self.nodes:
            for rule in node.acls:
                for endpoint in (rule.source, rule.destination):
                    if endpoint not in acl_targets:
                        raise ProvisioningSpecError(
                            f"ACESPS-008: ACL endpoint {endpoint!r} is neither a declared network nor a reserved token"
                        )
        return self


def validate_provisioning_spec(payload: object) -> ProvisioningSpec:
    """Validate an untrusted mapping into a :class:`ProvisioningSpec`.

    Wraps Pydantic's ``ValidationError`` in :class:`ProvisioningSpecError` so
    callers on the realization side see one bounded error type.
    """
    try:
        return ProvisioningSpec.model_validate(payload)
    except ProvisioningSpecError:
        raise
    except Exception as exc:
        # Normalize Pydantic ValidationError (and any other construction error)
        # to the single bounded error type the realization side handles.
        raise ProvisioningSpecError(f"invalid provisioning spec: {exc}") from exc


def provisioning_spec_json_schema() -> dict:
    """Return the JSON Schema for the locked contract (syntactic layer)."""
    return ProvisioningSpec.model_json_schema()
