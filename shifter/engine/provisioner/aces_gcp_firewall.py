"""Realize authored ACES node ACLs as GCE firewall rules (ADR-031, ADR-032).

The reference backend renders a node's ``acls`` as libvirt ``nwfilter`` rules;
the GCE backend renders them as Compute Engine firewall rules targeting a
per-node network tag. Translation is fail-closed, mirroring
``aces_backend_libvirt.acls``: a *specified* ``from_net``/``to_net`` that does not
resolve to a concrete CIDR raises rather than widening into a broad allow; an
*omitted* endpoint is the plan's own "any" (``0.0.0.0/0``).

Precedence policy (deliberate): authored ACLs are placed at priority
``1000 + author-index`` -- author order preserved (lower number wins), and
strictly *below* the base management firewall (priority 900) that grants the
provisioner SSH/RDP reachability. An authored "deny all inbound" therefore
segments participant traffic without severing the provisioner's own management
plane (host setup, verification, teardown), exactly as the cyberscript backend
keeps its management rule authoritative.
"""

from __future__ import annotations

from aces_plan import AcesPlanAcl, AcesPlanNetwork, AcesPlanNode
from gcp_range_cell_plan import FirewallPlan, _short_resource_name

#: Authored-ACL firewalls sit at 1000+index: below the priority-900 management
#: rule (so provisioner reachability is never blocked) but authoritative over the
#: base per-subnet ingress; ``+index`` preserves author order (lower number wins).
_ACL_FIREWALL_BASE_PRIORITY = 1000
_ANY_CIDR = "0.0.0.0/0"


class AcesGceFirewallError(RuntimeError):
    """Raised when an authored ACL cannot be realized fail-closed as a GCE firewall."""


def node_tag(range_id: int, node_address: str) -> str:
    """Return the per-node network tag ACL firewalls target this node's instances by."""
    return _short_resource_name("shifter-node", range_id, node_address)


def acl_cidr_lookup(networks: tuple[AcesPlanNetwork, ...]) -> dict[str, str]:
    """Map every handle an ACL might reference a network by to its CIDR.

    Mirrors ``aces_backend_libvirt.realization._network_cidr_lookup``: each network
    with a CIDR is keyed by its address, name, and address leaf.
    """
    lookup: dict[str, str] = {}
    for network in networks:
        if not network.cidr:
            continue
        for key in (network.address, network.name, network.address.rsplit(".", 1)[-1]):
            if key:
                lookup[key] = network.cidr
    return lookup


def _resolve_endpoint(ref: str | None, cidr_lookup: dict[str, str]) -> str:
    """Resolve an ACL endpoint ref to a CIDR; omitted = any, unresolvable = fail-closed."""
    if ref is None:
        return _ANY_CIDR
    cidr = cidr_lookup.get(ref)
    if not cidr:
        raise AcesGceFirewallError(f"ACL endpoint {ref!r} references a network with no resolvable CIDR")
    return cidr


def _acl_rule(acl: AcesPlanAcl) -> dict[str, object]:
    """Render the protocol/ports clause for one ACL firewall rule."""
    if acl.protocol == "all":
        return {"IPProtocol": "all"}
    rule: dict[str, object] = {"IPProtocol": acl.protocol}
    if acl.ports:
        rule["ports"] = [str(port) for port in acl.ports]
    return rule


def _acl_firewall(range_id: int, node_name: str, tag: str, acl: AcesPlanAcl, index: int, ingress: bool) -> FirewallPlan:
    """Render one directional GCE firewall for an authored ACL targeting a node tag."""
    rule_key = "allowed" if acl.action == "accept" else "denied"
    direction_label = "in" if ingress else "out"
    firewall: FirewallPlan = {
        "name": _short_resource_name("shifter-r", range_id, node_name, "acl", index, direction_label),
        "direction": "INGRESS" if ingress else "EGRESS",
        "priority": _ACL_FIREWALL_BASE_PRIORITY + index,
        "target_tags": [tag],
        rule_key: [_acl_rule(acl)],
    }
    return firewall


def build_acl_firewalls(range_id: int, node: AcesPlanNode, tag: str, cidr_lookup: dict[str, str]) -> list[FirewallPlan]:
    """Render every GCE firewall realizing one node's authored ACLs (fail-closed)."""
    firewalls: list[FirewallPlan] = []
    for index, acl in enumerate(node.acls):
        if acl.direction in ("in", "inout"):
            firewall = _acl_firewall(range_id, node.name, tag, acl, index, ingress=True)
            firewall["source_ranges"] = [_resolve_endpoint(acl.from_net, cidr_lookup)]
            firewalls.append(firewall)
        if acl.direction in ("out", "inout"):
            firewall = _acl_firewall(range_id, node.name, tag, acl, index, ingress=False)
            firewall["destination_ranges"] = [_resolve_endpoint(acl.to_net, cidr_lookup)]
            firewalls.append(firewall)
    return firewalls
