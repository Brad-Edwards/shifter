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
platform-side drift test compares this module's extraction against the reference
backend so the two cannot diverge.

Sizing/image are exposed as ``None`` when the author omitted them, so the backend
applies its own default (e.g. a GCE profile machine type) rather than a forced
constant. It self-discriminates on the plan ``kind`` so an ``aces-range`` command
run against a cyberscript ``range_config`` fails loudly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Must equal ``shared.aces.runtime_target.ACES_PROVISIONING_PLAN_KIND``.
ACES_PROVISIONING_PLAN_KIND = "aces_provisioning_plan"
NODE_RESOURCE_TYPE = "node"
NETWORK_RESOURCE_TYPE = "network"
CONTENT_RESOURCE_TYPE = "content-placement"
FEATURE_RESOURCE_TYPE = "feature-binding"
ACCOUNT_RESOURCE_TYPE = "account-placement"

_MIB = 1024 * 1024

#: ACL vocabulary, mirrored from aces_backend_libvirt.acls (the reference backend).
_ACL_ACTIONS = {"allow": "accept", "accept": "accept", "deny": "drop", "drop": "drop"}
_ACL_WILDCARD_PROTOCOLS = frozenset({"", "all", "any"})
_ACL_DIRECTIONS = frozenset({"in", "out", "inout"})


class AcesPlanError(ValueError):
    """Raised when a persisted range_config is not a well-formed serialized ACES plan."""


@dataclass(frozen=True)
class AcesPlanImage:
    """Authored image reference (from ACES ``source``); resolved to a concrete
    provider image by the backend at realization (ADR-032-R2)."""

    name: str
    version: str | None = None


@dataclass(frozen=True)
class AcesPlanAcl:
    """A node's authored network ACL (mirror aces_backend_libvirt.acls fields).

    ``action`` is normalized to ``accept``/``drop`` and ``protocol`` to
    ``tcp``/``udp``/``all``; ``from_net``/``to_net`` are kept as the authored
    network refs (resolved to concrete CIDRs at realization, fail-closed, so an
    unresolvable *specified* endpoint is never widened into a broad allow).
    """

    name: str
    action: str
    direction: str
    protocol: str
    ports: tuple[int, ...]
    from_net: str | None = None
    to_net: str | None = None


@dataclass(frozen=True)
class AcesPlanNode:
    """A compute node to provision, with authored intent extracted verbatim."""

    address: str
    name: str
    os_family: str
    count: int
    network_addresses: tuple[str, ...]
    ram_mib: int | None = None
    vcpus: int | None = None
    image: AcesPlanImage | None = None
    acls: tuple[AcesPlanAcl, ...] = ()


@dataclass(frozen=True)
class AcesPlanNetwork:
    """A network the range's nodes attach to."""

    address: str
    name: str
    cidr: str | None = None
    gateway: str | None = None
    internal: bool = False


@dataclass(frozen=True)
class AcesPlanContent:
    """A content placement (file/dataset/directory) targeting one node.

    ``text`` is inline file content (realized as a real file). Non-inline content
    (a ``source`` package, or dataset ``items``) is supplied by the baked image /
    guest repo at ``path``/``destination`` (ADR-032 baked-image delivery), so the
    realizer creates the structural target but does not fetch bytes.
    """

    name: str
    content_type: str
    target_address: str
    path: str | None = None
    destination: str | None = None
    text: str | None = None
    source_name: str | None = None
    file_format: str | None = None
    items: tuple[str, ...] = ()
    sensitive: bool = False


@dataclass(frozen=True)
class AcesPlanAccount:
    """A user account placement targeting one node."""

    username: str
    target_address: str
    groups: tuple[str, ...] = ()
    shell: str | None = None
    home: str | None = None
    mail: str | None = None
    spn: str | None = None
    auth_method: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class AcesPlanFeature:
    """A feature binding (service/artifact/configuration) targeting one node.

    A ``service`` feature realizes as an install+enable step whose package/artifact
    is provided by the baked image or the guest package repo (ADR-032); the backend
    does not fetch it.
    """

    name: str
    feature_type: str
    target_address: str
    source_name: str | None = None
    destination: str | None = None


@dataclass(frozen=True)
class AcesPlan:
    """The parsed serialized ACES plan: nodes + networks + composition for realization."""

    aces_sdl_version: str
    nodes: tuple[AcesPlanNode, ...]
    networks: tuple[AcesPlanNetwork, ...]
    content: tuple[AcesPlanContent, ...] = ()
    accounts: tuple[AcesPlanAccount, ...] = ()
    features: tuple[AcesPlanFeature, ...] = ()


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


def _network_lookup(networks: list[tuple[str, Mapping[str, Any]]]) -> dict[str, str]:
    """Map every handle a node might reference a network by to its canonical address."""
    lookup: dict[str, str] = {}
    for address, payload in networks:
        name = _resource_name(address, payload)
        for key in (address, name, address.rsplit(".", 1)[-1]):
            if key:
                lookup[key] = address
    return lookup


def _opt_str(value: object) -> str | None:
    """Return a stripped non-empty string, or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_tuple(value: object) -> tuple[str, ...]:
    """Return the non-empty strings in a list/tuple value as a tuple."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _source_name(spec: Mapping[str, Any]) -> str | None:
    """Return a ``source`` package name from a spec (string shorthand or {name})."""
    source = spec.get("source")
    if isinstance(source, str):
        return _opt_str(source)
    if isinstance(source, Mapping):
        return _opt_str(source.get("name"))
    return None


def _content_item_names(raw: object) -> tuple[str, ...]:
    """Return the ``name`` of each dataset item in a content ``items`` list."""
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(name for entry in raw if isinstance(entry, Mapping) and (name := _opt_str(entry.get("name"))))


def _content(payload: Mapping[str, Any]) -> AcesPlanContent | None:
    """Build an AcesPlanContent from a content-placement payload (None if malformed)."""
    spec = _mapping(payload.get("spec"))
    content_type = _opt_str(spec.get("type"))
    target = _opt_str(payload.get("target_address")) or _opt_str(payload.get("target_node"))
    if content_type is None or target is None:
        return None
    text = spec.get("text")
    return AcesPlanContent(
        name=_opt_str(payload.get("content_name")) or _opt_str(payload.get("name")) or content_type.lower(),
        content_type=content_type.lower(),
        target_address=target,
        path=_opt_str(spec.get("path")),
        destination=_opt_str(spec.get("destination")),
        text=text if isinstance(text, str) else None,
        source_name=_source_name(spec),
        file_format=_opt_str(spec.get("format")),
        items=_content_item_names(spec.get("items")),
        sensitive=spec.get("sensitive") is True,
    )


def _account(payload: Mapping[str, Any]) -> AcesPlanAccount | None:
    """Build an AcesPlanAccount from an account-placement payload (None if malformed)."""
    spec = _mapping(payload.get("spec"))
    username = _opt_str(spec.get("username")) or _opt_str(payload.get("account_name"))
    target = _opt_str(payload.get("target_address")) or _opt_str(payload.get("node_name"))
    if username is None or target is None:
        return None
    return AcesPlanAccount(
        username=username,
        target_address=target,
        groups=_str_tuple(spec.get("groups")),
        shell=_opt_str(spec.get("shell")),
        home=_opt_str(spec.get("home")),
        mail=_opt_str(spec.get("mail")),
        spn=_opt_str(spec.get("spn")),
        auth_method=_opt_str(spec.get("auth_method")),
        disabled=spec.get("disabled") is True,
    )


def _feature(payload: Mapping[str, Any]) -> AcesPlanFeature | None:
    """Build an AcesPlanFeature from a feature-binding payload (None if malformed)."""
    template = _mapping(_mapping(payload.get("spec")).get("template"))
    feature_type = _opt_str(template.get("type"))
    target = _opt_str(payload.get("node_address")) or _opt_str(payload.get("node_name"))
    name = _opt_str(payload.get("feature_name")) or _opt_str(template.get("name"))
    if feature_type is None or target is None or name is None:
        return None
    return AcesPlanFeature(
        name=name,
        feature_type=feature_type.lower(),
        target_address=target,
        source_name=_source_name(template),
        destination=_opt_str(template.get("destination")),
    )


def parse_plan(range_config: dict[str, Any] | None) -> AcesPlan:
    """Parse a serialized ACES plan from a range_config dict.

    Self-discriminates on ``kind`` so an ``aces-range`` command run against a
    cyberscript (or otherwise foreign) ``range_config`` raises rather than
    realizing an unintended topology.
    """
    envelope = _require_mapping(range_config, where="range_config")
    kind = envelope.get("kind")
    if kind != ACES_PROVISIONING_PLAN_KIND:
        raise AcesPlanError(f"kind must be {ACES_PROVISIONING_PLAN_KIND!r}, got {kind!r}")

    resources = _require_mapping(envelope.get("resources"), where="resources")
    network_pairs: list[tuple[str, Mapping[str, Any]]] = []
    node_pairs: list[tuple[str, Mapping[str, Any]]] = []
    composition: dict[str, list[Mapping[str, Any]]] = {
        CONTENT_RESOURCE_TYPE: [],
        FEATURE_RESOURCE_TYPE: [],
        ACCOUNT_RESOURCE_TYPE: [],
    }
    for entry in resources.values():
        entry_map = _require_mapping(entry, where="resource")
        address = _string(entry_map.get("address"), where="resource.address")
        payload = _require_mapping(entry_map.get("payload"), where="resource.payload")
        resource_type = entry_map.get("resource_type")
        if resource_type == NETWORK_RESOURCE_TYPE:
            network_pairs.append((address, payload))
        elif resource_type == NODE_RESOURCE_TYPE:
            node_pairs.append((address, payload))
        elif resource_type in composition:
            composition[resource_type].append(payload)

    lookup = _network_lookup(network_pairs)
    networks = tuple(_network(address, payload) for address, payload in sorted(network_pairs))
    nodes = tuple(_node(address, payload, lookup) for address, payload in sorted(node_pairs))

    version = envelope.get("aces_sdl_version")
    return AcesPlan(
        aces_sdl_version=version if isinstance(version, str) else "",
        nodes=nodes,
        networks=networks,
        content=tuple(c for p in composition[CONTENT_RESOURCE_TYPE] if (c := _content(p)) is not None),
        accounts=tuple(a for p in composition[ACCOUNT_RESOURCE_TYPE] if (a := _account(p)) is not None),
        features=tuple(f for p in composition[FEATURE_RESOURCE_TYPE] if (f := _feature(p)) is not None),
    )


def _acl_str(value: object) -> str:
    """Return ``value`` if it is a string, else an empty string."""
    return value if isinstance(value, str) else ""


def _acl_action(raw: Mapping[str, Any]) -> str:
    """Normalize an ACL action to ``accept``/``drop`` (mirror the reference)."""
    token = _acl_str(raw.get("action")).lower()
    if not token:
        raise AcesPlanError("ACL missing 'action'")
    if token not in _ACL_ACTIONS:
        raise AcesPlanError(f"ACL unknown action {token!r}")
    return _ACL_ACTIONS[token]


def _acl_protocol(raw: Mapping[str, Any]) -> str:
    """Normalize an ACL protocol to ``tcp``/``udp``/``all`` (mirror the reference)."""
    token = _acl_str(raw.get("protocol")).lower()
    if token in {"tcp", "udp"}:
        return token
    if token in _ACL_WILDCARD_PROTOCOLS:
        return "all"
    raise AcesPlanError(f"ACL unknown protocol {token!r}")


def _acl_direction(raw: Mapping[str, Any]) -> str:
    """Return the ACL direction (``in``/``out``/``inout``); default ``inout``."""
    token = _acl_str(raw.get("direction")).lower()
    if not token:
        return "inout"
    if token not in _ACL_DIRECTIONS:
        raise AcesPlanError(f"ACL unknown direction {token!r}")
    return token


def _acl_ports(raw: Mapping[str, Any]) -> tuple[int, ...]:
    """Return the ACL's validated integer ports (1-65535), rejecting bools/invalid."""
    raw_ports = raw.get("ports", ())
    if not isinstance(raw_ports, list | tuple):
        raise AcesPlanError("ACL 'ports' is not a list")
    ports: list[int] = []
    for port in raw_ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port <= 65535:
            raise AcesPlanError(f"ACL invalid port {port!r}")
        ports.append(port)
    return tuple(ports)


def _acl_ref(raw: Mapping[str, Any], key: str) -> str | None:
    """Return the raw endpoint network ref (``from_net``/``to_net``); None if omitted."""
    ref = _acl_str(raw.get(key)).strip()
    return ref or None


def _acl(raw: object, index: int) -> AcesPlanAcl:
    """Build one AcesPlanAcl, failing loud (fail-closed) on any invalid field."""
    if not isinstance(raw, Mapping):
        raise AcesPlanError(f"ACL #{index} is not an object")
    protocol = _acl_protocol(raw)
    ports = _acl_ports(raw)
    if ports and protocol == "all":
        raise AcesPlanError(f"ACL #{index} ports require protocol 'tcp' or 'udp'")
    return AcesPlanAcl(
        name=_acl_str(raw.get("name")).strip() or f"acl-{index}",
        action=_acl_action(raw),
        direction=_acl_direction(raw),
        protocol=protocol,
        ports=ports,
        from_net=_acl_ref(raw, "from_net"),
        to_net=_acl_ref(raw, "to_net"),
    )


def _node_acls(payload: Mapping[str, Any]) -> tuple[AcesPlanAcl, ...]:
    """Extract a node's authored ACLs from ``spec.infrastructure.acls``."""
    raw_acls = _infrastructure_spec(payload).get("acls")
    if not isinstance(raw_acls, list | tuple):
        return ()
    return tuple(_acl(raw, index) for index, raw in enumerate(raw_acls))


def _node(address: str, payload: Mapping[str, Any], lookup: dict[str, str]) -> AcesPlanNode:
    """Build an AcesPlanNode, extracting authored intent and resolving network refs."""
    resolved = tuple(dict.fromkeys(lookup[ref] for ref in _network_refs(payload) if ref in lookup))
    return AcesPlanNode(
        address=address,
        name=_resource_name(address, payload),
        os_family=_os_family(payload),
        count=_node_count(payload),
        network_addresses=resolved,
        ram_mib=_memory_mib(payload),
        vcpus=_vcpus(payload),
        image=_image(payload),
        acls=_node_acls(payload),
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
