"""Serialized, explicitly scoped Cloud NAT enrollment for shared range VPCs."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager

from gcp_range_cell_clients import GCEClients
from gcp_range_cell_naming import range_router_nat_plan
from gcp_range_cell_ops import _get_or_none, _wait_for_operation
from gcp_range_cell_types import RangeCellPlan
from provisioner_db import get_db_connection

_SUBNETS_PER_GATEWAY = 50
_GATEWAYS_PER_ROUTER = 50


def _canonical_subnet_link(link: str) -> str:
    """Normalize provider full and planned relative self-links to one identity."""
    _prefix, separator, suffix = link.partition("projects/")
    if not separator or "/regions/" not in suffix or "/subnetworks/" not in suffix:
        raise RuntimeError("shared-nat-invalid-subnet-link")
    return f"projects/{suffix}"


def allocate_explicit_subnets(
    existing: Mapping[str, Sequence[str]], requested: Sequence[str], *, nat_name: str
) -> list[dict[str, object]]:
    """Preserve existing assignments while packing new subnets into bounded gateways."""
    slots = {name: [_canonical_subnet_link(link) for link in links] for name, links in existing.items()}
    seen = {link for links in slots.values() for link in links}
    for raw_link in requested:
        link = _canonical_subnet_link(raw_link)
        if link in seen:
            continue
        destination = next((name for name, links in slots.items() if len(links) < _SUBNETS_PER_GATEWAY), None)
        if destination is None:
            if len(slots) >= _GATEWAYS_PER_ROUTER:
                raise RuntimeError("shared-nat-capacity-exhausted")
            destination = next(
                f"{nat_name}-{index}" for index in range(_GATEWAYS_PER_ROUTER) if f"{nat_name}-{index}" not in slots
            )
            slots[destination] = []
        slots[destination].append(link)
        seen.add(link)
    return [
        {
            "name": name,
            "nat_ip_allocate_option": "AUTO_ONLY",
            "source_subnetwork_ip_ranges_to_nat": "LIST_OF_SUBNETWORKS",
            "subnetworks": [{"name": link, "source_ip_ranges_to_nat": ["ALL_IP_RANGES"]} for link in links],
        }
        for name, links in slots.items()
        if links
    ]


@contextmanager
def _regional_nat_lock(plan: RangeCellPlan) -> Iterator[None]:
    """Serialize all provider NAT mutations for one shared network and region."""
    scope = f"{plan['project_id']}|{plan['region']}|{plan['network']['self_link']}".encode()
    key = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '30s'")
        cursor.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (key,))


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _items(value: object) -> Iterable[object]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return value
    return ()


def _router(plan: RangeCellPlan, clients: GCEClients, name: str) -> object | None:
    return _get_or_none(
        clients.routers.get,
        clients.google_exceptions,
        project=plan["project_id"],
        region=plan["region"],
        router=name,
    )


def _gateway_subnets(plan: RangeCellPlan, router: object) -> dict[str, list[str]]:
    network = str(_field(router, "network") or "")
    if not network.endswith(plan["network"]["self_link"]):
        raise RuntimeError("shared-nat-network-mismatch")
    expected_prefix = f"{plan['shared_nat']['nat_name']}-"
    result: dict[str, list[str]] = {}
    for gateway in _items(_field(router, "nats")):
        name = str(_field(gateway, "name") or "")
        mode = _field(gateway, "source_subnetwork_ip_ranges_to_nat")
        mode_name = getattr(mode, "name", str(mode))
        if not name.startswith(expected_prefix) or mode_name != "LIST_OF_SUBNETWORKS":
            raise RuntimeError("shared-nat-ownership-mismatch")
        result[name] = [
            _canonical_subnet_link(str(_field(item, "name") or "")) for item in _items(_field(gateway, "subnetworks"))
        ]
    return result


def _bridge_router_names(plan: RangeCellPlan) -> set[str]:
    """Terraform's pre-migration NAT has a deterministic name on the range VPC."""
    network_name = str(plan["network"].get("name") or plan["network"]["self_link"].rsplit("/", 1)[-1])
    return {f"{network_name}-nat", f"{network_name}-nat-{plan['region']}"}


def _bridge_replay(plan: RangeCellPlan, clients: GCEClients, routers: Iterable[object]) -> bool:
    """Keep an existing range on Terraform's bridge; never enroll a new subnet there."""
    nat = plan.get("shared_nat")
    if nat is None:
        return False
    desired = {_canonical_subnet_link(link) for link in nat["subnetwork_self_links"]}
    covered: set[str] = set()
    for router in routers:
        if str(_field(router, "name") or "") not in _bridge_router_names(plan):
            continue
        for gateway in _items(_field(router, "nats")):
            allocation = _field(gateway, "nat_ip_allocate_option")
            if getattr(allocation, "name", str(allocation)) != "MANUAL_ONLY":
                continue
            scope = _field(gateway, "source_subnetwork_ip_ranges_to_nat")
            if getattr(scope, "name", str(scope)) != "LIST_OF_SUBNETWORKS":
                continue
            covered.update(
                _canonical_subnet_link(str(_field(subnet, "name") or ""))
                for subnet in _items(_field(gateway, "subnetworks"))
            )
    if not desired or not desired.issubset(covered):
        return False
    for link in desired:
        subnet = _get_or_none(
            clients.subnetworks.get,
            clients.google_exceptions,
            project=plan["project_id"],
            region=plan["region"],
            subnetwork=link.rsplit("/", 1)[-1],
        )
        if subnet is None or not str(_field(subnet, "network") or "").endswith(plan["network"]["self_link"]):
            return False
    return True


def _verify_membership(plan: RangeCellPlan, clients: GCEClients, *, present: bool) -> None:
    nat = plan["shared_nat"]
    router = _router(plan, clients, nat["router_name"])
    actual = set()
    if router is not None:
        actual = {link for links in _gateway_subnets(plan, router).values() for link in links}
    requested = set(nat["subnetwork_self_links"])
    if (present and not requested.issubset(actual)) or (not present and requested.intersection(actual)):
        raise RuntimeError("shared-nat-reconciliation-incomplete")


def assert_shared_nat_capacity(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Reject quota exhaustion or a conflicting NAT before any range resource is made."""
    if plan["manage_network"]:
        return
    desired = {str(subnet["self_link"]) for subnet in plan["subnets"]}
    own = plan.get("shared_nat")
    legacy_name = str(range_router_nat_plan(plan["range_id"], [])["router_name"])
    with _regional_nat_lock(plan):
        regional = clients.routers.list(project=plan["project_id"], region=plan["region"])
        same_network = [
            router for router in regional if str(_field(router, "network") or "").endswith(plan["network"]["self_link"])
        ]
        bridge_replay = _bridge_replay(plan, clients, same_network)
        shared_exists = False
        legacy_exists = False
        for router in same_network:
            name = str(_field(router, "name") or "")
            shared_exists |= own is not None and name == own["router_name"]
            legacy_exists |= name == legacy_name
            for gateway in _items(_field(router, "nats")):
                scope = _field(gateway, "source_subnetwork_ip_ranges_to_nat")
                mode = getattr(scope, "name", str(scope))
                if mode != "LIST_OF_SUBNETWORKS":
                    raise RuntimeError("shared-nat-unbounded-or-unknown-scope")
                listed = {str(_field(subnet, "name") or "") for subnet in _items(_field(gateway, "subnetworks"))}
                overlap = any(any(link.endswith(want) for link in listed) for want in desired)
                bridge_owner = bridge_replay and name in _bridge_router_names(plan)
                if overlap and (own is None or name not in {own["router_name"], legacy_name}) and not bridge_owner:
                    raise RuntimeError("shared-nat-foreign-overlap")
                if overlap and own is None:
                    raise RuntimeError("shared-nat-zero-egress-conflict")
        if own is None or legacy_exists or bridge_replay:
            return
        if not shared_exists and len(same_network) >= 5:
            raise RuntimeError("shared-nat-router-capacity-exhausted")
        current = _router(plan, clients, own["router_name"])
        enrolled = _gateway_subnets(plan, current) if current is not None else {}
        allocate_explicit_subnets(enrolled, own["subnetwork_self_links"], nat_name=own["nat_name"])


def ensure_shared_nat(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Enroll this range's subnets in a provisioner-owned, explicit regional NAT."""
    nat = plan.get("shared_nat")
    if nat is None:
        return
    with _regional_nat_lock(plan):
        regional = clients.routers.list(project=plan["project_id"], region=plan["region"])
        if _bridge_replay(plan, clients, regional):
            return  # Terraform retains the old subnet until the operator drains its bridge.
        legacy_name = range_router_nat_plan(plan["range_id"], [])["router_name"]
        if _router(plan, clients, str(legacy_name)) is not None:
            return  # Existing range: keep its old independent NAT until teardown.
        current = _router(plan, clients, nat["router_name"])
        enrolled = _gateway_subnets(plan, current) if current is not None else {}
        gateways = allocate_explicit_subnets(enrolled, nat["subnetwork_self_links"], nat_name=nat["nat_name"])
        if current is None:
            operation = clients.routers.insert(
                project=plan["project_id"],
                region=plan["region"],
                router_resource={
                    "name": nat["router_name"],
                    "network": plan["network"]["self_link"],
                    "region": plan["region"],
                    "nats": gateways,
                },
            )
        elif {link for links in enrolled.values() for link in links} >= set(nat["subnetwork_self_links"]):
            return
        else:
            operation = clients.routers.patch(
                project=plan["project_id"],
                region=plan["region"],
                router=nat["router_name"],
                router_resource={"nats": gateways},
            )
        _wait_for_operation(plan, clients, operation, "region")
        _verify_membership(plan, clients, present=True)


def remove_shared_nat(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Remove this range's NAT entries before its subnets are deleted."""
    nat = plan.get("shared_nat")
    if nat is None:
        return
    with _regional_nat_lock(plan):
        regional = clients.routers.list(project=plan["project_id"], region=plan["region"])
        if _bridge_replay(plan, clients, regional):
            raise RuntimeError("shared-nat-migration-bridge-attached")
        legacy_name = str(range_router_nat_plan(plan["range_id"], [])["router_name"])
        if _router(plan, clients, legacy_name) is not None:
            operation = clients.routers.delete(project=plan["project_id"], region=plan["region"], router=legacy_name)
            _wait_for_operation(plan, clients, operation, "region")
            return
        current = _router(plan, clients, nat["router_name"])
        if current is None:
            return
        enrolled = _gateway_subnets(plan, current)
        requested = set(nat["subnetwork_self_links"])
        remaining = {name: [link for link in links if link not in requested] for name, links in enrolled.items()}
        if remaining == enrolled:
            return
        gateways = allocate_explicit_subnets(remaining, [], nat_name=nat["nat_name"])
        if gateways:
            operation = clients.routers.patch(
                project=plan["project_id"],
                region=plan["region"],
                router=nat["router_name"],
                router_resource={"nats": gateways},
            )
        else:
            operation = clients.routers.delete(
                project=plan["project_id"], region=plan["region"], router=nat["router_name"]
            )
        _wait_for_operation(plan, clients, operation, "region")
        _verify_membership(plan, clients, present=False)
