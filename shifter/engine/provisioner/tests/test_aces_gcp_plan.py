"""Tests for the ACES-native GCE range-cell plan builder (ADR-031, ADR-032).

Exercises the topology -> RangeCellPlan mapping: network mode (vpc-per-range vs
shared-vpc), authored CIDRs -> subnets, nodes -> instances (count fan-out,
deterministic IP assignment skipping GCP-reserved addresses, registry-resolved
image profile, neutral labels only), and fail-loud placement errors. The image
resolver is injected (pure), so no registry/DB is touched.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aces_gcp_firewall import node_tag
from aces_gcp_plan import AcesGcePlanError, build_aces_range_cell_plan
from aces_plan import AcesPlan, AcesPlanAcl, AcesPlanImage, AcesPlanNetwork, AcesPlanNode
from config import GCERangeCellConfig, GCERangeImageProfile


def _config(*, network_mode: str = "vpc-per-range", network_id: str = "") -> GCERangeCellConfig:
    return GCERangeCellConfig(
        project_id="proj-1",
        region="us-east1",
        zone="us-east1-b",
        network_mode=network_mode,
        network_id=network_id,
        portal_network_cidrs=("203.0.113.0/24",),
    )


def _network(address: str = "net.a", *, name: str = "lan", cidr: str = "10.0.0.0/24") -> AcesPlanNetwork:
    return AcesPlanNetwork(address=address, name=name, cidr=cidr)


def _node(
    address: str = "node.a",
    *,
    name: str = "victim",
    count: int = 1,
    networks: tuple[str, ...] = ("net.a",),
    os_family: str = "linux",
    acls: tuple[AcesPlanAcl, ...] = (),
) -> AcesPlanNode:
    return AcesPlanNode(
        address=address,
        name=name,
        os_family=os_family,
        count=count,
        network_addresses=networks,
        image=AcesPlanImage(name="kali"),
        acls=acls,
    )


def _plan(nodes: tuple[AcesPlanNode, ...], networks: tuple[AcesPlanNetwork, ...]) -> AcesPlan:
    return AcesPlan(aces_sdl_version="0.19.1", nodes=nodes, networks=networks)


def _resolver(profile: GCERangeImageProfile | None = None):
    resolved = profile or GCERangeImageProfile(source_image="projects/x/global/images/kali-1")
    return lambda node: resolved


class TestNetworkMode:
    def test_vpc_per_range_manages_its_own_network(self):
        plan = build_aces_range_cell_plan("req-1", 7, _plan((_node(),), (_network(),)), _resolver(), _config())
        assert plan["manage_network"] is True
        assert plan["network"]["name"] == "shifter-range-7"

    def test_shared_vpc_reuses_platform_network(self):
        config = _config(network_mode="shared-vpc", network_id="projects/proj-1/global/networks/shared")
        plan = build_aces_range_cell_plan("req-1", 7, _plan((_node(),), (_network(),)), _resolver(), config)
        assert plan["manage_network"] is False
        assert plan["network"]["name"] == "shared"


class TestSubnets:
    def test_subnet_from_authored_cidr(self):
        plan = build_aces_range_cell_plan(
            "req-1", 7, _plan((_node(),), (_network(cidr="10.9.0.0/24"),)), _resolver(), _config()
        )
        subnet = plan["subnets"][0]
        assert subnet["cidr"] == "10.9.0.0/24"
        assert subnet["name"] == "lan"
        assert subnet["uuid"] == "net.a"

    def test_network_without_cidr_fails_loud(self):
        networks = (AcesPlanNetwork(address="net.a", name="lan", cidr=None),)
        with pytest.raises(AcesGcePlanError, match="no cidr"):
            build_aces_range_cell_plan("req-1", 7, _plan((_node(),), networks), _resolver(), _config())


class TestInstances:
    def test_single_node_gets_first_usable_ip(self):
        plan = build_aces_range_cell_plan(
            "req-1", 7, _plan((_node(),), (_network(cidr="10.0.0.0/24"),)), _resolver(), _config()
        )
        instance = plan["instances"][0]
        # GCP reserves .0/.1/.2 (net, gw, reserved) and the top two; first usable is .3.
        assert instance["private_ip"] == "10.0.0.3"
        assert instance["role"] == "aces-node"
        assert instance["os_type"] == "linux"
        assert instance["profile"].source_image == "projects/x/global/images/kali-1"

    def test_count_fans_out_to_distinct_instances_and_ips(self):
        node = _node(count=3)
        plan = build_aces_range_cell_plan("req-1", 7, _plan((node,), (_network(),)), _resolver(), _config())
        instances = plan["instances"]
        assert len(instances) == 3
        assert [i["name"] for i in instances] == ["victim-0", "victim-1", "victim-2"]
        assert sorted(i["private_ip"] for i in instances) == ["10.0.0.3", "10.0.0.4", "10.0.0.5"]
        assert len({i["resource_name"] for i in instances}) == 3

    def test_windows_os_family_drives_os_type(self):
        node = _node(os_family="windows")
        plan = build_aces_range_cell_plan("req-1", 7, _plan((node,), (_network(),)), _resolver(), _config())
        assert plan["instances"][0]["os_type"] == "windows"

    def test_too_many_instances_for_subnet_fails_loud(self):
        # /30 has zero usable guest addresses after GCP reserves both ends.
        networks = (_network(cidr="10.0.0.0/30"),)
        with pytest.raises(AcesGcePlanError, match="usable addresses"):
            build_aces_range_cell_plan("req-1", 7, _plan((_node(),), networks), _resolver(), _config())


class TestPlacementErrors:
    def test_node_without_network_fails_loud(self):
        node = _node(networks=())
        with pytest.raises(AcesGcePlanError, match="no network"):
            build_aces_range_cell_plan("req-1", 7, _plan((node,), (_network(),)), _resolver(), _config())

    def test_node_referencing_undeclared_network_fails_loud(self):
        node = _node(networks=("net.missing",))
        with pytest.raises(AcesGcePlanError, match="undeclared network"):
            build_aces_range_cell_plan("req-1", 7, _plan((node,), (_network(),)), _resolver(), _config())


class TestFirewalls:
    def test_base_range_firewalls_are_present(self):
        plan = build_aces_range_cell_plan("req-1", 7, _plan((_node(),), (_network(),)), _resolver(), _config())
        names = {fw["name"] for fw in plan["firewalls"]}
        # Reused neutral base plan: per-subnet ingress + egress posture.
        assert any("ingress" in name for name in names)
        assert any("egress-deny" in name for name in names)

    def test_authored_acls_realized_as_node_firewalls(self):
        acl = AcesPlanAcl(
            name="ssh", action="accept", direction="in", protocol="tcp", ports=(22,), from_net="net.a", to_net=None
        )
        node = _node(acls=(acl,))
        plan = build_aces_range_cell_plan("req-1", 7, _plan((node,), (_network(),)), _resolver(), _config())
        acl_firewalls = [fw for fw in plan["firewalls"] if fw.get("target_tags") == [node_tag(7, "node.a")]]
        assert len(acl_firewalls) == 1
        assert acl_firewalls[0]["direction"] == "INGRESS"
        assert acl_firewalls[0]["allowed"] == [{"IPProtocol": "tcp", "ports": ["22"]}]

    def test_instance_carries_node_tag_for_acl_targeting(self):
        plan = build_aces_range_cell_plan("req-1", 7, _plan((_node(),), (_network(),)), _resolver(), _config())
        assert node_tag(7, "node.a") in plan["instances"][0]["tags"]
