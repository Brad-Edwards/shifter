"""Shared-VPC NAT allocation must scale without widening subnet scope."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import gcp_range_cell_shared_nat as shared_nat
from gcp_range_cell_naming import shared_router_nat_plan
from gcp_range_cell_shared_nat import (
    allocate_explicit_subnets,
    assert_shared_nat_capacity,
    ensure_shared_nat,
    remove_shared_nat,
)


def test_shared_gateway_name_fits_provider_limit_for_long_network_name():
    nat = shared_router_nat_plan("a" * 63, ["projects/p/regions/r/subnetworks/range-1"])
    gateway = allocate_explicit_subnets({}, nat["subnetwork_self_links"], nat_name=nat["nat_name"])[0]

    assert len(gateway["name"]) <= 63


def test_twenty_two_two_subnet_ranges_fit_one_gateway_without_global_nat():
    subnets = [f"projects/p/regions/r/subnetworks/range-{seat}-{side}" for seat in range(22) for side in range(2)]

    gateways = allocate_explicit_subnets({}, subnets, nat_name="shifter-shared-nat")

    assert len(gateways) == 1
    assert gateways[0]["source_subnetwork_ip_ranges_to_nat"] == "LIST_OF_SUBNETWORKS"
    assert [item["name"] for item in gateways[0]["subnetworks"]] == subnets


def test_gateway_sharding_stops_at_explicit_subnet_limit():
    subnets = [f"projects/p/regions/r/subnetworks/range-{index}" for index in range(51)]

    gateways = allocate_explicit_subnets({}, subnets, nat_name="shifter-shared-nat")

    assert [len(gateway["subnetworks"]) for gateway in gateways] == [50, 1]


def test_capacity_exhaustion_refuses_before_any_widening():
    subnets = [f"projects/p/regions/r/subnetworks/range-{index}" for index in range(2501)]

    with pytest.raises(RuntimeError, match="shared-nat-capacity-exhausted"):
        allocate_explicit_subnets({}, subnets, nat_name="shifter-shared-nat")


def test_gateway_reuse_never_overwrites_an_occupied_sparse_slot():
    existing = {
        "shifter-shared-nat-1": [f"projects/p/regions/r/subnetworks/a-{index}" for index in range(50)],
        "shifter-shared-nat-2": [f"projects/p/regions/r/subnetworks/b-{index}" for index in range(50)],
    }

    gateways = allocate_explicit_subnets(
        existing, ["projects/p/regions/r/subnetworks/new"], nat_name="shifter-shared-nat"
    )

    assert {gateway["name"] for gateway in gateways} == set(existing) | {"shifter-shared-nat-0"}
    assert sum(len(gateway["subnetworks"]) for gateway in gateways) == 101


def test_provider_full_self_link_replay_does_not_duplicate_relative_subnet():
    relative = "projects/p/regions/r/subnetworks/range-1"
    existing = {"shifter-shared-nat-0": [f"https://www.googleapis.com/compute/v1/{relative}"]}

    gateways = allocate_explicit_subnets(existing, [relative], nat_name="shifter-shared-nat")

    assert len(gateways[0]["subnetworks"]) == 1


def _plan_for_capacity(*, egress: bool = True) -> dict:
    plan = {
        "project_id": "p",
        "region": "r",
        "range_id": 7,
        "manage_network": False,
        "network": {"self_link": "projects/p/global/networks/shared"},
        "subnets": [{"self_link": "projects/p/regions/r/subnetworks/range-7"}],
    }
    if egress:
        plan["shared_nat"] = {
            "router_name": "shifter-shared-nat-router",
            "nat_name": "shifter-shared-nat",
            "subnetwork_self_links": [plan["subnets"][0]["self_link"]],
        }
    return plan


def test_none_range_is_refused_if_a_foreign_all_subnet_nat_exists(monkeypatch):
    monkeypatch.setattr(shared_nat, "_regional_nat_lock", lambda _plan: nullcontext())
    router = {
        "name": "foreign-router",
        "network": "projects/p/global/networks/shared",
        "nats": [{"source_subnetwork_ip_ranges_to_nat": "ALL_SUBNETWORKS_ALL_IP_RANGES"}],
    }
    clients = SimpleNamespace(routers=SimpleNamespace(list=lambda **_kwargs: [router]))

    with pytest.raises(RuntimeError, match="shared-nat-unbounded-or-unknown-scope"):
        assert_shared_nat_capacity(_plan_for_capacity(egress=False), clients)


def test_router_limit_is_checked_before_creating_range_subnets(monkeypatch):
    class NotFound(Exception):
        pass

    monkeypatch.setattr(shared_nat, "_regional_nat_lock", lambda _plan: nullcontext())
    routers = [
        {"name": f"other-{index}", "network": "projects/p/global/networks/shared", "nats": []} for index in range(5)
    ]

    def get(**_kwargs):
        raise NotFound()

    clients = SimpleNamespace(
        routers=SimpleNamespace(list=lambda **_kwargs: routers, get=get),
        google_exceptions=SimpleNamespace(NotFound=NotFound),
    )

    with pytest.raises(RuntimeError, match="shared-nat-router-capacity-exhausted"):
        assert_shared_nat_capacity(_plan_for_capacity(), clients)


def test_existing_bridge_subnet_replay_preserves_terraform_nat(monkeypatch):
    class NotFound(Exception):
        pass

    monkeypatch.setattr(shared_nat, "_regional_nat_lock", lambda _plan: nullcontext())
    plan = _plan_for_capacity()
    plan["network"]["name"] = "shared"
    subnet = plan["subnets"][0]["self_link"]
    bridge = {
        "name": "shared-nat",
        "network": plan["network"]["self_link"],
        "nats": [
            {
                "name": "shared-nat",
                "nat_ip_allocate_option": "MANUAL_ONLY",
                "source_subnetwork_ip_ranges_to_nat": "LIST_OF_SUBNETWORKS",
                "subnetworks": [{"name": f"https://www.googleapis.com/compute/v1/{subnet}"}],
            }
        ],
    }

    def get_router(**_kwargs):
        raise NotFound()

    clients = SimpleNamespace(
        routers=SimpleNamespace(list=lambda **_kwargs: [bridge], get=get_router),
        subnetworks=SimpleNamespace(get=lambda **_kwargs: {"network": plan["network"]["self_link"]}),
        google_exceptions=SimpleNamespace(NotFound=NotFound),
    )

    assert_shared_nat_capacity(plan, clients)
    ensure_shared_nat(plan, clients)
    with pytest.raises(RuntimeError, match="shared-nat-migration-bridge-attached"):
        remove_shared_nat(plan, clients)


def test_bridge_cannot_enroll_a_new_range_subnet(monkeypatch):
    class NotFound(Exception):
        pass

    monkeypatch.setattr(shared_nat, "_regional_nat_lock", lambda _plan: nullcontext())
    plan = _plan_for_capacity()
    subnet = plan["subnets"][0]["self_link"]
    bridge = {
        "name": "shared-nat",
        "network": plan["network"]["self_link"],
        "nats": [
            {
                "name": "shared-nat",
                "nat_ip_allocate_option": "MANUAL_ONLY",
                "source_subnetwork_ip_ranges_to_nat": "LIST_OF_SUBNETWORKS",
                "subnetworks": [{"name": subnet}],
            }
        ],
    }

    def missing_subnet(**_kwargs):
        raise NotFound()

    clients = SimpleNamespace(
        routers=SimpleNamespace(list=lambda **_kwargs: [bridge]),
        subnetworks=SimpleNamespace(get=missing_subnet),
        google_exceptions=SimpleNamespace(NotFound=NotFound),
    )

    with pytest.raises(RuntimeError, match="shared-nat-foreign-overlap"):
        assert_shared_nat_capacity(plan, clients)


def test_convergent_enrollment_and_removal_preserve_other_ranges(monkeypatch):
    class NotFound(Exception):
        pass

    routers = {}
    service = MagicMock()

    def get(**kwargs):
        try:
            return routers[kwargs["router"]]
        except KeyError:
            raise NotFound() from None

    def insert(**kwargs):
        body = kwargs["router_resource"]
        routers[body["name"]] = body
        return None

    def patch(**kwargs):
        routers[kwargs["router"]] = {**routers[kwargs["router"]], **kwargs["router_resource"]}
        return None

    def delete(**kwargs):
        routers.pop(kwargs["router"])
        return None

    service.get.side_effect = get
    service.insert.side_effect = insert
    service.patch.side_effect = patch
    service.delete.side_effect = delete
    clients = SimpleNamespace(routers=service, google_exceptions=SimpleNamespace(NotFound=NotFound))
    monkeypatch.setattr(shared_nat, "_regional_nat_lock", lambda _plan: nullcontext())
    monkeypatch.setattr(shared_nat, "_wait_for_operation", lambda *_args: None)

    def plan(range_id):
        link = f"projects/p/regions/r/subnetworks/range-{range_id}"
        return {
            "project_id": "p",
            "region": "r",
            "range_id": range_id,
            "network": {"self_link": "projects/p/global/networks/shared"},
            "shared_nat": {
                "router_name": "shifter-shared-nat-router",
                "nat_name": "shifter-shared-nat",
                "subnetwork_self_links": [link],
            },
        }

    ensure_shared_nat(plan(1), clients)
    ensure_shared_nat(plan(2), clients)
    ensure_shared_nat(plan(1), clients)

    assert service.insert.call_count == 1
    assert service.patch.call_count == 1
    assert [s["name"] for s in routers["shifter-shared-nat-router"]["nats"][0]["subnetworks"]] == [
        "projects/p/regions/r/subnetworks/range-1",
        "projects/p/regions/r/subnetworks/range-2",
    ]

    remove_shared_nat(plan(1), clients)
    assert "shifter-shared-nat-router" in routers
    assert [s["name"] for s in routers["shifter-shared-nat-router"]["nats"][0]["subnetworks"]] == [
        "projects/p/regions/r/subnetworks/range-2"
    ]
    remove_shared_nat(plan(2), clients)
    assert not routers
