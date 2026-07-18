"""Provisioner-side reader for the serialized ACES ProvisioningPlan (ADR-031, ADR-032).

The ACES-native provisioning path persists the *serialized ACES ProvisioningPlan*
in ``mission_control_range.range_config`` (see
``shifter_platform/shared/aces/runtime_target.py::serialize_provisioning_plan``).
The provisioner is a separate deployable whose image ships only ``cyberscript`` on
``PYTHONPATH`` (no ``shared``, no Pydantic) and must not import any ``aces_*`` SDL
package (ADR-024). So it reads the plan as plain data here.

Per ADR-032, Shifter does not re-model the plan into a Shifter-owned spec: this
module reads the ACES plan payloads via accessors that **mirror the reference
ACES backend** ``aces_backend_libvirt`` (``_payload.py`` / ``realization.py``) --
``os_family`` from ``payload.os_family`` then ``spec.node.os``; the image from
``spec.node.source`` (name verbatim); sizing from ``spec.node.resources.ram``
(bytes -> MiB) and ``.cpu``; network membership from ``spec.infrastructure``. A
platform-side contract test (``tests/shared/aces/test_plan_provisioner_parity.py``)
guards this module's extraction against Shifter-owned fixtures; upstream ACES
convention changes are bounded by the supported ``aces-sdl`` version window
(ADR-032-R7) and re-validated when it is raised, rather than by a live differential
against the reference backend's private accessors.

Sizing/image are exposed as ``None`` when the author omitted them, so the backend
applies its own default (e.g. a GCE profile machine type) rather than a forced
constant. It self-discriminates on the plan ``kind`` so an ``aces-range`` command
run against a cyberscript ``range_config`` fails loudly. The frozen value objects
live in ``aces_plan_types`` and ACL parsing in ``aces_acl`` (Sonar file-size split);
they are re-exported here so callers keep importing from ``aces_plan``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast

from aces_acl import build_node_acls
from aces_composition import (
    AcesPlanAccount,
    AcesPlanContent,
    AcesPlanFeature,
    build_account,
    build_content,
    build_feature,
)
from aces_plan_types import (
    AcesPlan,
    AcesPlanAcl,
    AcesPlanDomain,
    AcesPlanError,
    AcesPlanImage,
    AcesPlanNetwork,
    AcesPlanNode,
    AcesPlanServicePort,
)
from aces_service import build_node_services

__all__ = [
    "ACES_PROVISIONING_PLAN_CONTRACT_VERSION",
    "MAXIMUM_ACES_SDL_VERSION_EXCLUSIVE",
    "MINIMUM_ACES_SDL_VERSION",
    "SUPPORTED_ACCOUNT_AUTH_METHODS",
    "SUPPORTED_CONTRACT_VERSIONS",
    "SUPPORTED_RESOURCE_TYPES",
    "AcesPlan",
    "AcesPlanAccount",
    "AcesPlanAcl",
    "AcesPlanContent",
    "AcesPlanDomain",
    "AcesPlanError",
    "AcesPlanFeature",
    "AcesPlanImage",
    "AcesPlanNetwork",
    "AcesPlanNode",
    "AcesPlanServicePort",
    "parse_plan",
]

#: Must equal ``shared.aces.runtime_target.ACES_PROVISIONING_PLAN_KIND``.
ACES_PROVISIONING_PLAN_KIND = "aces_provisioning_plan"
NODE_RESOURCE_TYPE = "node"
NETWORK_RESOURCE_TYPE = "network"
CONTENT_RESOURCE_TYPE = "content-placement"
FEATURE_RESOURCE_TYPE = "feature-binding"
ACCOUNT_RESOURCE_TYPE = "account-placement"

#: Resource types this consumer realizes; any other type in the plan fails closed
#: (ADR-032-R7). Mirrors ``shared.aces.runtime_target.SUPPORTED_RESOURCE_TYPES``.
SUPPORTED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {NODE_RESOURCE_TYPE, NETWORK_RESOURCE_TYPE, CONTENT_RESOURCE_TYPE, FEATURE_RESOURCE_TYPE, ACCOUNT_RESOURCE_TYPE}
)

#: Serialized-plan transport contract version this consumer accepts (ADR-032-R7).
#: The provisioner image ships without ``shared`` and must not import ``aces_*``
#: (ADR-024), so this is a Shifter-owned literal kept in lockstep with the producer
#: stamp ``shared.aces.contracts.ACES_PROVISIONING_PLAN_CONTRACT_VERSION`` by a
#: platform-side parity test (mirroring the ``ACES_PROVISIONING_PLAN_KIND`` pattern).
#: A new transport envelope shape is a new ``-vN`` member of the supported set.
ACES_PROVISIONING_PLAN_CONTRACT_VERSION = "aces-provisioning-plan-v1"
SUPPORTED_CONTRACT_VERSIONS: frozenset[str] = frozenset({ACES_PROVISIONING_PLAN_CONTRACT_VERSION})

#: Supported ``aces-sdl`` producer series this consumer accepts, as a bounded
#: half-open range ``[MINIMUM, MAXIMUM_EXCLUSIVE)`` (ADR-032-R7 fail-closed: an
#: unknown future release is rejected, not assumed compatible). The 0.19.1 floor
#: remains readable for already-persisted plans during rolling deployment, while
#: the platform producer is pinned exactly to 0.23.0. A platform-side test asserts
#: that exact producer pin equals the installed version and lies in this window.
#: Adopting a new series requires raising this window and passing the ACES
#: conformance gate.
MINIMUM_ACES_SDL_VERSION = "0.19.1"
MAXIMUM_ACES_SDL_VERSION_EXCLUSIVE = "0.24.0"

#: Duplicated intentionally across the separate deployable boundary and pinned
#: to ``shared.aces.composition_envelope`` by a producer/consumer parity test.
SUPPORTED_ACCOUNT_AUTH_METHODS: frozenset[str] = frozenset({"password", "publickey"})
SUPPORTED_PASSWORD_STRENGTHS: frozenset[str] = frozenset({"weak", "medium", "strong", "none"})
_NO_CREDENTIAL_STRENGTH = "none"
_DOMAIN_ACCOUNT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,19}$")
_SPN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_DNS_NAME = re.compile(
    r"(?=^.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_NETBIOS_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,13}[A-Za-z0-9])?$")

_MIB = 1024 * 1024


def _mapping(value: object) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the payload's ``spec`` mapping, or an empty mapping."""
    return _mapping(payload.get("spec"))


def _node_spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the payload's ``spec.node`` mapping, or an empty mapping."""
    return _mapping(_spec(payload).get("node"))


def _infrastructure_spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the payload's ``spec.infrastructure`` mapping, or an empty mapping."""
    return _mapping(_spec(payload).get("infrastructure"))


def _resource_name(address: str, payload: Mapping[str, Any]) -> str:
    """Return the authored resource name, falling back to the address leaf."""
    name = payload.get("name") or payload.get("node_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return address.rsplit(".", 1)[-1]


def _os_family(payload: Mapping[str, Any]) -> str:
    """Mirror aces_backend_libvirt._os_family: os_family, else spec.node.os."""
    family = payload.get("os_family")
    if isinstance(family, str) and family:
        return family
    node_os = _node_spec(payload).get("os")
    return node_os if isinstance(node_os, str) else ""


def _node_count(payload: Mapping[str, Any]) -> int:
    """Return the node instance count (>= 1); default 1 for missing/invalid values."""
    raw = payload.get("count")
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int) and raw >= 1:
        return raw
    return 1


def _memory_mib(payload: Mapping[str, Any]) -> int | None:
    """Authored RAM -> MiB (mirror aces_backend_libvirt._memory_mib); None if absent."""
    raw = _mapping(_node_spec(payload).get("resources")).get("ram")
    if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0:
        if raw >= _MIB:
            return max(128, int((raw + _MIB - 1) // _MIB))
        return max(128, int(raw))
    return None


def _vcpus(payload: Mapping[str, Any]) -> int | None:
    """Authored CPU -> vcpus (mirror aces_backend_libvirt._vcpus); None if absent."""
    raw = _mapping(_node_spec(payload).get("resources")).get("cpu")
    if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0:
        return max(1, int(raw))
    return None


def _image(payload: Mapping[str, Any]) -> AcesPlanImage | None:
    """Authored image from spec.node.source (name verbatim, mirror _image_ref)."""
    source = _node_spec(payload).get("source")
    if isinstance(source, str) and source.strip():
        return AcesPlanImage(name=source.strip())
    if isinstance(source, Mapping):
        name = source.get("name")
        if isinstance(name, str) and name.strip():
            version = source.get("version")
            return AcesPlanImage(
                name=name.strip(),
                version=version.strip() if isinstance(version, str) and version.strip() else None,
            )
    return None


def _network_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the network handles a node references (``networks`` then ``links``)."""
    infra = _infrastructure_spec(payload)
    for field_name in ("networks", "links"):
        raw = infra.get(field_name)
        if isinstance(raw, list | tuple):
            return tuple(ref for ref in raw if isinstance(ref, str) and ref.strip())
    return ()


def _network(address: str, payload: Mapping[str, Any]) -> AcesPlanNetwork:
    """Build an AcesPlanNetwork from a network resource payload (cidr/gateway/internal)."""
    props = _mapping(_infrastructure_spec(payload).get("properties"))
    cidr = props.get("cidr")
    gateway = props.get("gateway")
    return AcesPlanNetwork(
        address=address,
        name=_resource_name(address, payload),
        cidr=cidr.strip() if isinstance(cidr, str) and cidr.strip() else None,
        gateway=gateway.strip() if isinstance(gateway, str) and gateway.strip() else None,
        internal=props.get("internal") is True,
    )


def _identity_lookup(resources: list[tuple[str, Mapping[str, Any]]], kind: str) -> dict[str, str]:
    """Map every handle a resource may be referenced by to its canonical address.

    Handles are the canonical address, the authored name, and the address leaf.
    Fails closed (ADR-032-R7) when two distinct resources of ``kind`` share a
    handle, since a reference to that handle would be ambiguous.
    """
    lookup: dict[str, str] = {}
    for address, payload in resources:
        name = _resource_name(address, payload)
        for key in (address, name, address.rsplit(".", 1)[-1]):
            if not key:
                continue
            existing = lookup.get(key)
            if existing is not None and existing != address:
                raise AcesPlanError(f"duplicate {kind} alias {key!r} maps to {existing!r} and {address!r}")
            lookup[key] = address
    return lookup


#: A strict dotted-numeric release: ``MAJOR[.MINOR[.PATCH...]]`` with no pre-release
#: or build suffix. Anything else (``0.19.1rc1``, ``1garbage``, ``not-a-version``)
#: fails closed rather than being silently truncated to a numeric prefix.
_RELEASE_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def _release_tuple(version: str) -> tuple[int, ...]:
    """Parse a strict dotted-numeric release into an int tuple (fail closed).

    Only a pure ``MAJOR[.MINOR[.PATCH...]]`` string is accepted; a pre-release or
    build suffix, trailing text, or any non-numeric component raises
    :class:`AcesPlanError` rather than being accepted as a truncated prefix.
    """
    if not _RELEASE_VERSION_RE.fullmatch(version):
        raise AcesPlanError(f"aces_sdl_version {version!r} is not a valid release version")
    return tuple(int(segment) for segment in version.split("."))


def _validate_versions(envelope: Mapping[str, Any]) -> str:
    """Validate the transport contract and ``aces-sdl`` producer versions (ADR-032-R7).

    Returns the validated ``aces_sdl_version``; raises :class:`AcesPlanError` on an
    unsupported/absent contract version, or a missing/malformed producer version or
    one outside the bounded supported ``aces-sdl`` series -- so a version-skewed or
    unknown-future plan never reaches realization.
    """
    contract_version = envelope.get("contract_version")
    # Type-check the JSON discriminator before set membership: an unhashable value
    # (list/dict from a malformed envelope) must fail closed as AcesPlanError, not
    # raise TypeError outside the parser contract.
    if not isinstance(contract_version, str) or contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise AcesPlanError(
            f"unsupported contract_version {contract_version!r} (supported: {sorted(SUPPORTED_CONTRACT_VERSIONS)})"
        )
    version = envelope.get("aces_sdl_version")
    if not isinstance(version, str) or not version.strip():
        raise AcesPlanError("aces_sdl_version must be a non-empty string")
    version = version.strip()
    parsed = _release_tuple(version)
    if not _release_tuple(MINIMUM_ACES_SDL_VERSION) <= parsed < _release_tuple(MAXIMUM_ACES_SDL_VERSION_EXCLUSIVE):
        raise AcesPlanError(
            f"aces_sdl_version {version!r} is outside the supported range "
            f"[{MINIMUM_ACES_SDL_VERSION}, {MAXIMUM_ACES_SDL_VERSION_EXCLUSIVE})"
        )
    return version


def _build_composition[CompositionValue: (AcesPlanContent, AcesPlanAccount, AcesPlanFeature)](
    builder: Callable[[Mapping[str, Any]], CompositionValue | None],
    resource_type: str,
    pairs: list[tuple[str, Mapping[str, Any]]],
    node_lookup: dict[str, str],
    ordering_dependencies: Mapping[str, tuple[str, ...]],
) -> tuple[CompositionValue, ...]:
    """Build every composition value object of one kind, failing closed (ADR-032-R7).

    A payload missing required fields (``build_*`` returns ``None``) is a malformed
    resource; a placement whose ``target_address`` does not resolve to a declared
    node is a dangling composition reference. Both abort before an ``AcesPlan`` is
    returned, so credential/content bootstrap can never bind to an absent node.
    """
    built: list[CompositionValue] = []
    for address, payload in pairs:
        try:
            value = builder(payload)
        except ValueError as exc:
            raise AcesPlanError(str(exc)) from None
        if value is None:
            raise AcesPlanError(f"malformed {resource_type} resource at {address}")
        if isinstance(value, AcesPlanAccount):
            value = cast(
                CompositionValue,
                replace(
                    cast(AcesPlanAccount, value),
                    address=address,
                    ordering_dependencies=ordering_dependencies.get(address, ()),
                ),
            )
        if value.target_address not in node_lookup:
            raise AcesPlanError(f"{resource_type} resource at {address} targets unknown node {value.target_address!r}")
        built.append(value)
    return tuple(built)


def _validate_account_credentials(account: AcesPlanAccount) -> None:
    """Repeat account credential policy at the separate provisioner boundary."""
    if account.auth_method not in SUPPORTED_ACCOUNT_AUTH_METHODS:
        raise AcesPlanError("unsupported account auth_method")
    if account.auth_method == "password" and (
        account.password_strength not in SUPPORTED_PASSWORD_STRENGTHS
        or (account.password_strength == _NO_CREDENTIAL_STRENGTH and not account.disabled)
    ):
        raise AcesPlanError("unsupported password_strength for account credential")
    if account.mail is not None:
        raise AcesPlanError("account mail is not realized consistently across supported guest operating systems")
    if account.spn is not None and account.domain_ref is None:
        raise AcesPlanError("account spn requires a supported domain binding")
    if account.domain_ref is not None and (
        not _DOMAIN_ACCOUNT_NAME.fullmatch(account.username)
        or account.auth_method != "password"
        or account.password_strength not in {"weak", "medium", "strong"}
        or account.disabled
        or account.groups
        or account.login_shell is not None
        or account.home is not None
    ):
        raise AcesPlanError("domain account policy is unsupported by this provisioner")
    if account.spn is not None and (
        _SPN.fullmatch(account.spn) is None
        or account.spn.strip() != account.spn
        or "\n" in account.spn
        or "\r" in account.spn
    ):
        raise AcesPlanError("account spn is invalid for this provisioner")


def _dependencies(entry: Mapping[str, Any]) -> tuple[str, ...]:
    raw = entry.get("ordering_dependencies")
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple) or any(not isinstance(item, str) or not item for item in raw):
        raise AcesPlanError("resource ordering_dependencies must be a list of non-empty strings")
    return tuple(dict.fromkeys(raw))


def _topology(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = payload.get("domain_topology")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise AcesPlanError("domain topology must be an object")
    topology = dict(raw)
    required = (
        "domain_id",
        "profile",
        "dns_name",
        "netbios_name",
        "authority_account_address",
        "role",
    )
    if any(
        not _topology_text(topology, field) or _topology_text(topology, field).strip() != topology[field]
        for field in required
    ):
        raise AcesPlanError("domain topology identity is malformed")
    if topology["profile"] != "active_directory" or topology["role"] not in {"controller", "member"}:
        raise AcesPlanError("domain topology profile or role is unsupported")
    if _DNS_NAME.fullmatch(topology["dns_name"]) is None or _NETBIOS_NAME.fullmatch(topology["netbios_name"]) is None:
        raise AcesPlanError("domain topology naming is malformed")
    _topology_addresses(topology, "controller_addresses")
    return topology


def _topology_text(topology: Mapping[str, Any], field: str) -> str:
    value = topology.get(field)
    return value if isinstance(value, str) else ""


def _topology_addresses(topology: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = topology.get(field)
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) or not item for item in value):
        raise AcesPlanError("domain topology address list is malformed")
    addresses = tuple(value)
    if not addresses or len(addresses) != len(set(addresses)):
        raise AcesPlanError("domain topology address list is malformed")
    return addresses


def _topology_signature(topology: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        topology["profile"],
        topology["dns_name"],
        topology["netbios_name"],
        topology["authority_account_address"],
        _topology_addresses(topology, "controller_addresses"),
    )


def _build_domains(
    nodes: tuple[AcesPlanNode, ...], accounts: tuple[AcesPlanAccount, ...]
) -> tuple[AcesPlanDomain, ...]:
    """Build and revalidate the bounded process-local domain realization view."""
    nodes_by_address = {node.address: node for node in nodes}
    accounts_by_address = {account.address: account for account in accounts}
    domain_ids = sorted({node.domain_id for node in nodes if node.domain_id is not None})
    domains: list[AcesPlanDomain] = []
    for domain_id in domain_ids:
        domain_nodes = tuple(node for node in nodes if node.domain_id == domain_id)
        controllers = tuple(node for node in domain_nodes if node.domain_role == "controller")
        members = tuple(node for node in domain_nodes if node.domain_role == "member")
        if len(controllers) != 1 or controllers[0].count != 1 or controllers[0].os_family.lower() != "windows":
            raise AcesPlanError("domain controller cardinality or operating system is unsupported")
        controller = controllers[0]
        if any(member.os_family.lower() != "windows" for member in members):
            raise AcesPlanError("domain member operating system is unsupported")
        if any(
            controller.network_addresses
            and member.network_addresses
            and set(controller.network_addresses).isdisjoint(member.network_addresses)
            for member in members
        ):
            raise AcesPlanError("domain member is not reachable from its controller")
        if any(controller.address not in member.ordering_dependencies for member in members):
            raise AcesPlanError("domain member ordering dependency is missing")

        topology = _topology_for_node_payload(controller)
        profile = _topology_text(topology, "profile")
        controller_addresses = _topology_addresses(topology, "controller_addresses")
        authority_address = _topology_text(topology, "authority_account_address")
        if profile != "active_directory" or controller_addresses != (controller.address,):
            raise AcesPlanError("domain topology profile or controller binding is unsupported")
        authority = accounts_by_address.get(authority_address)
        if (
            authority is None
            or authority.domain_id != domain_id
            or authority.username.casefold() != "administrator"
            or authority.target_address != controller.address
            or authority.auth_method != "password"
            or authority.password_strength not in {"weak", "medium", "strong"}
            or authority.disabled
            or authority.groups
            or authority.login_shell is not None
            or authority.home is not None
            or authority.mail is not None
            or authority.spn is not None
            or authority.domain_ref is not None
        ):
            raise AcesPlanError("domain authority account is unsupported")

        domain_accounts = tuple(account for account in accounts if account.domain_ref == domain_id)
        if any(
            account.domain_id == domain_id and account.address != authority_address and account.domain_ref is None
            for account in accounts
        ):
            raise AcesPlanError("domain topology account binding is invalid")
        if any(account.domain_id != domain_id for account in domain_accounts):
            raise AcesPlanError("domain account binding is invalid")
        usernames = [authority.username.casefold(), *(account.username.casefold() for account in domain_accounts)]
        spns = [account.spn.casefold() for account in domain_accounts if account.spn]
        if len(usernames) != len(set(usernames)):
            raise AcesPlanError("duplicate domain account identity")
        if len(spns) != len(set(spns)):
            raise AcesPlanError("duplicate account spn")
        if any(
            account.target_address not in nodes_by_address
            or nodes_by_address[account.target_address].domain_id != domain_id
            for account in domain_accounts
        ):
            raise AcesPlanError("domain account target is invalid")

        domains.append(
            AcesPlanDomain(
                domain_id=domain_id,
                profile=profile,
                dns_name=_topology_text(topology, "dns_name"),
                netbios_name=_topology_text(topology, "netbios_name"),
                authority_account_address=authority_address,
                controller_addresses=controller_addresses,
                member_addresses=tuple(member.address for member in members),
            )
        )
    if any(account.domain_ref is not None and account.domain_ref not in domain_ids for account in accounts):
        raise AcesPlanError("domain account references an unsupported domain")
    return tuple(domains)


def _topology_for_node_payload(node: AcesPlanNode) -> Mapping[str, Any]:
    """Return the validated topology carrier retained on a parsed node."""
    return {
        "domain_id": node.domain_id or "",
        "profile": getattr(node, "domain_profile", None) or "active_directory",
        "dns_name": getattr(node, "domain_dns_name", None) or "",
        "netbios_name": getattr(node, "domain_netbios_name", None) or "",
        "authority_account_address": getattr(node, "authority_account_address", None) or "",
        "controller_addresses": list(node.controller_addresses),
    }


def parse_plan(range_config: dict[str, Any] | None) -> AcesPlan:
    """Parse a serialized ACES plan from a range_config dict, failing closed.

    Self-discriminates on ``kind`` so an ``aces-range`` command run against a
    cyberscript (or otherwise foreign) ``range_config`` raises rather than
    realizing an unintended topology. It then validates the transport contract and
    ``aces-sdl`` producer versions and every topology term (ADR-032-R7): unknown
    resource types, malformed payloads, duplicate identities/aliases, and dangling
    network references all raise before any ``AcesPlan`` is returned -- i.e. before
    the caller reaches ``apply_aces_range_cell`` / ``destroy_aces_range_cell`` and
    any cloud mutation.
    """
    envelope = _require_mapping(range_config, where="range_config")
    kind = envelope.get("kind")
    if kind != ACES_PROVISIONING_PLAN_KIND:
        raise AcesPlanError(f"kind must be {ACES_PROVISIONING_PLAN_KIND!r}, got {kind!r}")
    aces_sdl_version = _validate_versions(envelope)

    resources = _require_mapping(envelope.get("resources"), where="resources")
    network_pairs: list[tuple[str, Mapping[str, Any]]] = []
    node_pairs: list[tuple[str, Mapping[str, Any]]] = []
    composition: dict[str, list[tuple[str, Mapping[str, Any]]]] = {
        CONTENT_RESOURCE_TYPE: [],
        FEATURE_RESOURCE_TYPE: [],
        ACCOUNT_RESOURCE_TYPE: [],
    }
    seen_addresses: set[str] = set()
    ordering_dependencies: dict[str, tuple[str, ...]] = {}
    topology_signatures: dict[str, tuple[object, ...]] = {}
    for entry in resources.values():
        entry_map = _require_mapping(entry, where="resource")
        address = _string(entry_map.get("address"), where="resource.address")
        if address in seen_addresses:
            raise AcesPlanError(f"duplicate resource address {address!r}")
        seen_addresses.add(address)
        payload = _require_mapping(entry_map.get("payload"), where="resource.payload")
        ordering_dependencies[address] = _dependencies(entry_map)
        topology = _topology(payload)
        if topology:
            domain_id = _topology_text(topology, "domain_id")
            signature = _topology_signature(topology)
            if domain_id in topology_signatures and topology_signatures[domain_id] != signature:
                raise AcesPlanError("domain topology identity is inconsistent")
            topology_signatures[domain_id] = signature
        resource_type = entry_map.get("resource_type")
        # Type-check the JSON discriminator before any membership test so an
        # unhashable/non-string value fails closed as AcesPlanError, not TypeError.
        if not isinstance(resource_type, str) or resource_type not in SUPPORTED_RESOURCE_TYPES:
            raise AcesPlanError(
                f"unsupported resource_type {resource_type!r} at {address} "
                f"(supported: {sorted(SUPPORTED_RESOURCE_TYPES)})"
            )
        if resource_type == NETWORK_RESOURCE_TYPE:
            network_pairs.append((address, payload))
        elif resource_type == NODE_RESOURCE_TYPE:
            node_pairs.append((address, payload))
        else:
            composition[resource_type].append((address, payload))

    network_lookup = _identity_lookup(network_pairs, NETWORK_RESOURCE_TYPE)
    node_lookup = _identity_lookup(node_pairs, NODE_RESOURCE_TYPE)
    networks = tuple(_network(address, payload) for address, payload in sorted(network_pairs))
    nodes = tuple(
        _node(address, payload, network_lookup, ordering_dependencies.get(address, ()))
        for address, payload in sorted(node_pairs)
    )
    content = _build_composition(
        build_content, CONTENT_RESOURCE_TYPE, composition[CONTENT_RESOURCE_TYPE], node_lookup, ordering_dependencies
    )
    accounts = _build_composition(
        build_account, ACCOUNT_RESOURCE_TYPE, composition[ACCOUNT_RESOURCE_TYPE], node_lookup, ordering_dependencies
    )
    for account in accounts:
        _validate_account_credentials(account)
    features = _build_composition(
        build_feature, FEATURE_RESOURCE_TYPE, composition[FEATURE_RESOURCE_TYPE], node_lookup, ordering_dependencies
    )
    domains = _build_domains(nodes, accounts)

    return AcesPlan(
        aces_sdl_version=aces_sdl_version,
        nodes=nodes,
        networks=networks,
        content=content,
        accounts=accounts,
        features=features,
        domains=domains,
    )


def _node(
    address: str,
    payload: Mapping[str, Any],
    network_lookup: dict[str, str],
    ordering_dependencies: tuple[str, ...],
) -> AcesPlanNode:
    """Build an AcesPlanNode, resolving network membership and ACL endpoints.

    Fails closed (ADR-032-R7) on a network-membership ref or an ACL ``from_net`` /
    ``to_net`` endpoint that no declared network resolves, rather than silently
    dropping it (which would provision a wrong topology or an unintended ACL).
    """
    resolved: list[str] = []
    for ref in _network_refs(payload):
        target = network_lookup.get(ref)
        if target is None:
            raise AcesPlanError(f"node {address} references unknown network {ref!r}")
        resolved.append(target)
    acls = build_node_acls(_infrastructure_spec(payload).get("acls"))
    for acl in acls:
        for endpoint in (acl.from_net, acl.to_net):
            if endpoint is not None and endpoint not in network_lookup:
                raise AcesPlanError(f"node {address} ACL {acl.name!r} references unknown network {endpoint!r}")
    topology = _topology(payload)
    return AcesPlanNode(
        address=address,
        name=_resource_name(address, payload),
        os_family=_os_family(payload),
        count=_node_count(payload),
        network_addresses=tuple(dict.fromkeys(resolved)),
        ram_mib=_memory_mib(payload),
        vcpus=_vcpus(payload),
        image=_image(payload),
        acls=acls,
        services=build_node_services(_node_spec(payload).get("services")),
        ordering_dependencies=ordering_dependencies,
        domain_id=_topology_text(topology, "domain_id") or None,
        domain_role=_topology_text(topology, "role") or None,
        controller_addresses=_topology_addresses(topology, "controller_addresses") if topology else (),
        domain_profile=_topology_text(topology, "profile") or None,
        domain_dns_name=_topology_text(topology, "dns_name") or None,
        domain_netbios_name=_topology_text(topology, "netbios_name") or None,
        authority_account_address=_topology_text(topology, "authority_account_address") or None,
    )


def _require_mapping(value: object, *, where: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise AcesPlanError naming ``where``."""
    if not isinstance(value, Mapping):
        raise AcesPlanError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _string(value: object, *, where: str) -> str:
    """Return ``value`` as a non-empty string or raise AcesPlanError naming ``where``."""
    if not isinstance(value, str) or not value.strip():
        raise AcesPlanError(f"{where} must be a non-empty string")
    return value
