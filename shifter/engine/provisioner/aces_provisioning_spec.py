"""Provisioner-side reader for the neutral ACES ProvisioningSpec (ADR-031).

The ACES-native provisioning path persists a bare, self-describing
``ProvisioningSpec`` JSON in ``mission_control_range.range_config`` (see
``shifter_platform/shared/aces/provisioning_spec.py``, the LOCKED Pydantic
contract and validation authority). The provisioner is a separate deployable
whose image ships only ``cyberscript`` on ``PYTHONPATH`` (no ``shared``, no
Pydantic), so it cannot import the platform contract. It also must not import any
``aces_*`` SDL package (ADR-024).

This module is the provisioner's dependency-light, pure-stdlib mirror of that
contract: it parses the persisted dict into frozen dataclasses and
self-discriminates on ``contract_version`` / ``profile`` so running the
``aces-range`` command against a non-ACES ``range_config`` fails loudly rather
than realizing garbage. It carries **no** cyberscript concept (no ``scenario_id``,
``role``, or ``os_type`` enum) -- the ACES code path stays first-class conformant.

A platform-side differential test round-trips a real
``shared.aces.provisioning_spec.ProvisioningSpec`` through :func:`parse` to guard
the two representations against drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

#: Must equal ``shared.aces.provisioning_spec.PROVISIONING_SPEC_CONTRACT_VERSION``.
PROVISIONING_SPEC_CONTRACT_VERSION = "provisioning-spec-v1"
#: Must equal ``shared.aces.contracts.SHIFTER_BACKEND_PROFILE``.
PROVISIONING_ONLY_PROFILE = "provisioning-only"

_VALID_ACL_ACTIONS = frozenset({"allow", "deny"})
_VALID_ACL_DIRECTIONS = frozenset({"ingress", "egress", "both"})


class AcesProvisioningSpecError(ValueError):
    """Raised when a persisted ProvisioningSpec dict is missing or malformed."""


@dataclass(frozen=True)
class AcesResources:
    """Provider-neutral sizing hint (from ACES node ``resources``)."""

    ram_mib: int | None = None
    vcpus: int | None = None


@dataclass(frozen=True)
class AcesImage:
    """Provider-neutral authored image reference (from ACES ``source``)."""

    name: str
    version: str | None = None


@dataclass(frozen=True)
class AcesService:
    """A listening service on a node."""

    name: str
    port: int
    protocol: str = "tcp"


@dataclass(frozen=True)
class AcesAclRule:
    """A directional allow/deny rule on a node between network endpoints."""

    action: str
    direction: str
    source: str
    destination: str
    protocol: str = "any"
    ports: tuple[int, ...] = ()
    name: str | None = None


@dataclass(frozen=True)
class AcesNode:
    """A compute node (VM) to provision."""

    address: str
    name: str
    os_family: str
    count: int = 1
    resources: AcesResources = field(default_factory=AcesResources)
    image: AcesImage | None = None
    services: tuple[AcesService, ...] = ()
    network_addresses: tuple[str, ...] = ()
    acls: tuple[AcesAclRule, ...] = ()


@dataclass(frozen=True)
class AcesNetwork:
    """A network the range's nodes attach to."""

    address: str
    name: str
    cidr: str | None = None
    gateway: str | None = None
    internal: bool = False


@dataclass(frozen=True)
class AcesProvisioningSpec:
    """The parsed, neutral ACES provisioning specification."""

    request_id: str
    profile: str
    contract_version: str
    nodes: tuple[AcesNode, ...] = ()
    networks: tuple[AcesNetwork, ...] = ()


def _require_mapping(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcesProvisioningSpecError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _optional_int(value: Any, *, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AcesProvisioningSpecError(f"{where} must be an integer")
    return value


def _string(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcesProvisioningSpecError(f"{where} must be a non-empty string")
    return value


def _parse_resources(payload: dict[str, Any]) -> AcesResources:
    return AcesResources(
        ram_mib=_optional_int(payload.get("ram_mib"), where="resources.ram_mib"),
        vcpus=_optional_int(payload.get("vcpus"), where="resources.vcpus"),
    )


def _parse_image(payload: Any) -> AcesImage | None:
    if payload is None:
        return None
    mapping = _require_mapping(payload, where="node.image")
    version = mapping.get("version")
    return AcesImage(name=_string(mapping.get("name"), where="image.name"), version=version)


def _parse_service(payload: Any) -> AcesService:
    mapping = _require_mapping(payload, where="node.services[]")
    port = _optional_int(mapping.get("port"), where="service.port")
    if port is None:
        raise AcesProvisioningSpecError("service.port is required")
    return AcesService(
        name=_string(mapping.get("name"), where="service.name"),
        port=port,
        protocol=mapping.get("protocol", "tcp"),
    )


def _parse_acl(payload: Any) -> AcesAclRule:
    mapping = _require_mapping(payload, where="node.acls[]")
    action = _string(mapping.get("action"), where="acl.action").lower()
    if action not in _VALID_ACL_ACTIONS:
        raise AcesProvisioningSpecError(f"acl.action must be one of {sorted(_VALID_ACL_ACTIONS)}")
    direction = _string(mapping.get("direction"), where="acl.direction").lower()
    if direction not in _VALID_ACL_DIRECTIONS:
        raise AcesProvisioningSpecError(f"acl.direction must be one of {sorted(_VALID_ACL_DIRECTIONS)}")
    ports = tuple(_optional_int(p, where="acl.ports[]") or 0 for p in mapping.get("ports", ()))
    return AcesAclRule(
        action=action,
        direction=direction,
        source=_string(mapping.get("source"), where="acl.source"),
        destination=_string(mapping.get("destination"), where="acl.destination"),
        protocol=mapping.get("protocol", "any"),
        ports=ports,
        name=mapping.get("name"),
    )


def _parse_node(payload: Any) -> AcesNode:
    mapping = _require_mapping(payload, where="nodes[]")
    count = _optional_int(mapping.get("count", 1), where="node.count") or 1
    return AcesNode(
        address=_string(mapping.get("address"), where="node.address"),
        name=_string(mapping.get("name"), where="node.name"),
        os_family=_string(mapping.get("os_family"), where="node.os_family").lower(),
        count=count,
        resources=_parse_resources(_require_mapping(mapping.get("resources", {}), where="node.resources")),
        image=_parse_image(mapping.get("image")),
        services=tuple(_parse_service(s) for s in mapping.get("services", ())),
        network_addresses=tuple(
            _string(n, where="node.network_addresses[]") for n in mapping.get("network_addresses", ())
        ),
        acls=tuple(_parse_acl(a) for a in mapping.get("acls", ())),
    )


def _parse_network(payload: Any) -> AcesNetwork:
    mapping = _require_mapping(payload, where="networks[]")
    return AcesNetwork(
        address=_string(mapping.get("address"), where="network.address"),
        name=_string(mapping.get("name"), where="network.name"),
        cidr=mapping.get("cidr"),
        gateway=mapping.get("gateway"),
        internal=bool(mapping.get("internal", False)),
    )


def parse(payload: dict[str, Any] | None) -> AcesProvisioningSpec:
    """Parse a persisted range_config dict into an :class:`AcesProvisioningSpec`.

    Self-discriminates on ``contract_version`` and ``profile`` so an ``aces-range``
    command run against a cyberscript (or otherwise foreign) ``range_config``
    raises rather than realizing an unintended topology.
    """
    mapping = _require_mapping(payload, where="range_config")

    contract_version = mapping.get("contract_version")
    if contract_version != PROVISIONING_SPEC_CONTRACT_VERSION:
        raise AcesProvisioningSpecError(
            f"contract_version must be {PROVISIONING_SPEC_CONTRACT_VERSION!r}, got {contract_version!r}"
        )
    profile = mapping.get("profile")
    if profile != PROVISIONING_ONLY_PROFILE:
        raise AcesProvisioningSpecError(f"profile must be {PROVISIONING_ONLY_PROFILE!r}, got {profile!r}")

    request_id = _string(mapping.get("request_id"), where="request_id")
    try:
        UUID(request_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise AcesProvisioningSpecError("request_id must be a canonical UUID string") from exc

    return AcesProvisioningSpec(
        request_id=request_id,
        profile=profile,
        contract_version=contract_version,
        nodes=tuple(_parse_node(n) for n in mapping.get("nodes", ())),
        networks=tuple(_parse_network(n) for n in mapping.get("networks", ())),
    )
