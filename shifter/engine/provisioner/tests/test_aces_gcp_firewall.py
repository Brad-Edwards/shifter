"""Tests for authored-ACL -> GCE firewall realization (ADR-031, ADR-032).

Security-critical translation: verifies direction/action/protocol/ports mapping,
fail-closed endpoint resolution (omitted = any; unresolvable = raise, never a
broad allow), author-order priority, and the per-node target tag. Precedence vs
the base management plane is documented in aces_gcp_firewall.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aces_gcp_firewall import (
    AcesGceFirewallError,
    acl_cidr_lookup,
    build_acl_firewalls,
    node_tag,
)
from aces_plan import AcesPlanAcl, AcesPlanNetwork, AcesPlanNode


def _node(acls: tuple[AcesPlanAcl, ...], *, address: str = "node.web") -> AcesPlanNode:
    return AcesPlanNode(
        address=address,
        name=address.rsplit(".", 1)[-1],
        os_family="linux",
        count=1,
        network_addresses=("net.lan",),
        acls=acls,
    )


_LOOKUP = {"net.lan": "10.9.0.0/24", "lan": "10.9.0.0/24", "net.dmz": "10.9.1.0/24"}


def _acl(**kw) -> AcesPlanAcl:
    base = {
        "name": "r",
        "action": "accept",
        "direction": "in",
        "protocol": "all",
        "ports": (),
        "from_net": None,
        "to_net": None,
    }
    base.update(kw)
    return AcesPlanAcl(**base)


class TestAclCidrLookup:
    def test_keys_networks_by_address_name_and_leaf(self):
        networks = (AcesPlanNetwork(address="net.lan", name="lan", cidr="10.9.0.0/24"),)
        lookup = acl_cidr_lookup(networks)
        assert lookup == {"net.lan": "10.9.0.0/24", "lan": "10.9.0.0/24"}

    def test_networks_without_cidr_are_skipped(self):
        networks = (AcesPlanNetwork(address="net.lan", name="lan", cidr=None),)
        assert acl_cidr_lookup(networks) == {}


class TestDirectionAndAction:
    def test_allow_inbound_tcp_port(self):
        acl = _acl(action="accept", direction="in", protocol="tcp", ports=(22,), from_net="net.dmz")
        firewalls = build_acl_firewalls(7, _node((acl,)), node_tag(7, "node.web"), _LOOKUP)
        assert len(firewalls) == 1
        fw = firewalls[0]
        assert fw["direction"] == "INGRESS"
        assert fw["allowed"] == [{"IPProtocol": "tcp", "ports": ["22"]}]
        assert fw["source_ranges"] == ["10.9.1.0/24"]
        assert fw["target_tags"] == [node_tag(7, "node.web")]
        assert fw["priority"] == 1000

    def test_deny_outbound_uses_denied_and_destination_ranges(self):
        acl = _acl(action="drop", direction="out", protocol="all", to_net="net.dmz")
        fw = build_acl_firewalls(7, _node((acl,)), node_tag(7, "node.web"), _LOOKUP)[0]
        assert fw["direction"] == "EGRESS"
        assert fw["denied"] == [{"IPProtocol": "all"}]
        assert fw["destination_ranges"] == ["10.9.1.0/24"]
        assert "allowed" not in fw

    def test_inout_yields_ingress_and_egress(self):
        acl = _acl(direction="inout", protocol="all")
        firewalls = build_acl_firewalls(7, _node((acl,)), node_tag(7, "node.web"), _LOOKUP)
        directions = {fw["direction"] for fw in firewalls}
        assert directions == {"INGRESS", "EGRESS"}


class TestEndpointResolution:
    def test_omitted_endpoint_is_any(self):
        acl = _acl(direction="in", from_net=None)
        fw = build_acl_firewalls(7, _node((acl,)), node_tag(7, "node.web"), _LOOKUP)[0]
        assert fw["source_ranges"] == ["0.0.0.0/0"]

    def test_unresolvable_endpoint_fails_closed(self):
        acl = _acl(direction="in", from_net="net.ghost")
        with pytest.raises(AcesGceFirewallError, match="no resolvable CIDR"):
            build_acl_firewalls(7, _node((acl,)), node_tag(7, "node.web"), _LOOKUP)


class TestPriorityOrdering:
    def test_author_order_preserved_via_priority(self):
        acls = (
            _acl(direction="in", protocol="tcp", ports=(22,), from_net="net.dmz"),
            _acl(direction="in", action="drop", protocol="all", from_net="net.lan"),
        )
        firewalls = build_acl_firewalls(7, _node(acls), node_tag(7, "node.web"), _LOOKUP)
        assert [fw["priority"] for fw in firewalls] == [1000, 1001]
