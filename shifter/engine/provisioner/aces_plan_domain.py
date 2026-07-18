"""Domain/account policy for the serialized ACES plan consumer (ADR-032-R7).

This module is intentionally stdlib-only apart from provisioner-owned value
objects. It keeps the separately deployed provisioner's fail-closed copy of the
platform admission policy small enough to review and test independently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from aces_composition import AcesPlanAccount
from aces_plan_types import AcesPlanDomain, AcesPlanError, AcesPlanNode

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


def _unsupported_domain_account(account: AcesPlanAccount) -> bool:
    """Return whether a domain account requests an unsupported effect."""
    checks = (
        _DOMAIN_ACCOUNT_NAME.fullmatch(account.username) is not None,
        account.auth_method == "password",
        account.password_strength in {"weak", "medium", "strong"},
        not account.disabled,
        not account.groups,
        account.login_shell is None,
        account.home is None,
    )
    return not all(checks)


def _invalid_spn(spn: str) -> bool:
    """Return whether an SPN is not a canonical bounded single line."""
    checks = (_SPN.fullmatch(spn) is not None, spn.strip() == spn, "\n" not in spn, "\r" not in spn)
    return not all(checks)


def validate_account_credentials(account: AcesPlanAccount) -> None:
    """Repeat account credential policy at the separate provisioner boundary."""
    if account.auth_method not in SUPPORTED_ACCOUNT_AUTH_METHODS:
        raise AcesPlanError("unsupported account auth_method")
    invalid_password = account.password_strength not in SUPPORTED_PASSWORD_STRENGTHS
    enabled_without_password = account.password_strength == _NO_CREDENTIAL_STRENGTH and not account.disabled
    if account.auth_method == "password" and (invalid_password or enabled_without_password):
        raise AcesPlanError("unsupported password_strength for account credential")
    if account.mail is not None:
        raise AcesPlanError("account mail is not realized consistently across supported guest operating systems")
    if account.spn is not None and account.domain_ref is None:
        raise AcesPlanError("account spn requires a supported domain binding")
    if account.domain_ref is not None and _unsupported_domain_account(account):
        raise AcesPlanError("domain account policy is unsupported by this provisioner")
    if account.spn is not None and _invalid_spn(account.spn):
        raise AcesPlanError("account spn is invalid for this provisioner")


def topology_text(topology: Mapping[str, Any], field: str) -> str:
    """Return a topology string field and normalize every other shape to empty."""
    value = topology.get(field)
    return value if isinstance(value, str) else ""


def topology_addresses(topology: Mapping[str, Any], field: str) -> tuple[str, ...]:
    """Return a required non-empty topology address list without duplicates."""
    value = topology.get(field)
    malformed_item = isinstance(value, list | tuple) and any(not isinstance(item, str) or not item for item in value)
    if not isinstance(value, list | tuple) or malformed_item:
        raise AcesPlanError("domain topology address list is malformed")
    addresses = tuple(value)
    if not addresses or len(addresses) != len(set(addresses)):
        raise AcesPlanError("domain topology address list is malformed")
    return addresses


def topology(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate and return one serialized domain-topology carrier."""
    raw = payload.get("domain_topology")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise AcesPlanError("domain topology must be an object")
    result = dict(raw)
    required = ("domain_id", "profile", "dns_name", "netbios_name", "authority_account_address", "role")
    malformed = any(
        not topology_text(result, field) or topology_text(result, field).strip() != result[field] for field in required
    )
    if malformed:
        raise AcesPlanError("domain topology identity is malformed")
    if result["profile"] != "active_directory" or result["role"] not in {"controller", "member"}:
        raise AcesPlanError("domain topology profile or role is unsupported")
    names_valid = (
        _DNS_NAME.fullmatch(result["dns_name"]) is not None
        and _NETBIOS_NAME.fullmatch(result["netbios_name"]) is not None
    )
    if not names_valid:
        raise AcesPlanError("domain topology naming is malformed")
    topology_addresses(result, "controller_addresses")
    return result


def topology_signature(value: Mapping[str, Any]) -> tuple[object, ...]:
    """Return the domain identity fields that must agree on every carrier."""
    return (
        value["profile"],
        value["dns_name"],
        value["netbios_name"],
        value["authority_account_address"],
        topology_addresses(value, "controller_addresses"),
    )


def _topology_for_node(node: AcesPlanNode) -> Mapping[str, Any]:
    """Return the validated topology carrier retained on a parsed node."""
    return {
        "domain_id": node.domain_id or "",
        "profile": node.domain_profile or "active_directory",
        "dns_name": node.domain_dns_name or "",
        "netbios_name": node.domain_netbios_name or "",
        "authority_account_address": node.authority_account_address or "",
        "controller_addresses": list(node.controller_addresses),
    }


def _validated_controller(domain_nodes: tuple[AcesPlanNode, ...]) -> AcesPlanNode:
    """Return the one supported Windows controller instance."""
    controllers = tuple(node for node in domain_nodes if node.domain_role == "controller")
    valid = len(controllers) == 1 and controllers[0].count == 1 and controllers[0].os_family.lower() == "windows"
    if not valid:
        raise AcesPlanError("domain controller cardinality or operating system is unsupported")
    return controllers[0]


def _member_unreachable(controller: AcesPlanNode, member: AcesPlanNode) -> bool:
    """Return whether explicit controller/member networks are disjoint."""
    if not controller.network_addresses or not member.network_addresses:
        return False
    return set(controller.network_addresses).isdisjoint(member.network_addresses)


def _validated_members(domain_nodes: tuple[AcesPlanNode, ...], controller: AcesPlanNode) -> tuple[AcesPlanNode, ...]:
    """Return supported members after validating OS, reachability, and ordering."""
    members = tuple(node for node in domain_nodes if node.domain_role == "member")
    if any(member.os_family.lower() != "windows" for member in members):
        raise AcesPlanError("domain member operating system is unsupported")
    if any(_member_unreachable(controller, member) for member in members):
        raise AcesPlanError("domain member is not reachable from its controller")
    if any(controller.address not in member.ordering_dependencies for member in members):
        raise AcesPlanError("domain member ordering dependency is missing")
    return members


def _controller_binding(controller: AcesPlanNode) -> tuple[Mapping[str, Any], str, tuple[str, ...]]:
    """Return the controller topology and its exact authority/address binding."""
    value = _topology_for_node(controller)
    controller_addresses = topology_addresses(value, "controller_addresses")
    if topology_text(value, "profile") != "active_directory" or controller_addresses != (controller.address,):
        raise AcesPlanError("domain topology profile or controller binding is unsupported")
    return value, topology_text(value, "authority_account_address"), controller_addresses


def _valid_authority(
    authority: AcesPlanAccount | None,
    domain_id: str,
    controller_address: str,
) -> bool:
    """Return whether an account is the exact supported RID-500 authority shape."""
    if authority is None:
        return False
    checks = (
        authority.domain_id == domain_id,
        authority.username.casefold() == "administrator",
        authority.target_address == controller_address,
        authority.auth_method == "password",
        authority.password_strength in {"weak", "medium", "strong"},
        not authority.disabled,
        not authority.groups,
        authority.login_shell is None,
        authority.home is None,
        authority.mail is None,
        authority.spn is None,
        authority.domain_ref is None,
    )
    return all(checks)


def _validate_domain_accounts(
    domain_id: str,
    authority: AcesPlanAccount,
    authority_address: str,
    accounts: tuple[AcesPlanAccount, ...],
    nodes_by_address: Mapping[str, AcesPlanNode],
) -> None:
    """Validate account binding, uniqueness, and target membership for one domain."""
    domain_accounts = tuple(account for account in accounts if account.domain_ref == domain_id)
    unbound = any(
        account.domain_id == domain_id and account.address != authority_address and account.domain_ref is None
        for account in accounts
    )
    if unbound:
        raise AcesPlanError("domain topology account binding is invalid")
    if any(account.domain_id != domain_id for account in domain_accounts):
        raise AcesPlanError("domain account binding is invalid")
    usernames = [authority.username.casefold(), *(account.username.casefold() for account in domain_accounts)]
    spns = [account.spn.casefold() for account in domain_accounts if account.spn]
    if len(usernames) != len(set(usernames)):
        raise AcesPlanError("duplicate domain account identity")
    if len(spns) != len(set(spns)):
        raise AcesPlanError("duplicate account spn")
    invalid_target = any(
        account.target_address not in nodes_by_address
        or nodes_by_address[account.target_address].domain_id != domain_id
        for account in domain_accounts
    )
    if invalid_target:
        raise AcesPlanError("domain account target is invalid")


def _build_domain(
    domain_id: str,
    nodes: tuple[AcesPlanNode, ...],
    accounts: tuple[AcesPlanAccount, ...],
    nodes_by_address: Mapping[str, AcesPlanNode],
    accounts_by_address: Mapping[str, AcesPlanAccount],
) -> AcesPlanDomain:
    """Build one validated process-local domain realization view."""
    domain_nodes = tuple(node for node in nodes if node.domain_id == domain_id)
    controller = _validated_controller(domain_nodes)
    members = _validated_members(domain_nodes, controller)
    value, authority_address, controller_addresses = _controller_binding(controller)
    authority = accounts_by_address.get(authority_address)
    if not _valid_authority(authority, domain_id, controller.address):
        raise AcesPlanError("domain authority account is unsupported")
    assert authority is not None
    _validate_domain_accounts(domain_id, authority, authority_address, accounts, nodes_by_address)
    return AcesPlanDomain(
        domain_id=domain_id,
        profile=topology_text(value, "profile"),
        dns_name=topology_text(value, "dns_name"),
        netbios_name=topology_text(value, "netbios_name"),
        authority_account_address=authority_address,
        controller_addresses=controller_addresses,
        member_addresses=tuple(member.address for member in members),
    )


def build_domains(nodes: tuple[AcesPlanNode, ...], accounts: tuple[AcesPlanAccount, ...]) -> tuple[AcesPlanDomain, ...]:
    """Build and revalidate every bounded process-local domain realization view."""
    nodes_by_address = {node.address: node for node in nodes}
    accounts_by_address = {account.address: account for account in accounts}
    domain_ids = sorted({node.domain_id for node in nodes if node.domain_id is not None})
    domains = tuple(
        _build_domain(domain_id, nodes, accounts, nodes_by_address, accounts_by_address) for domain_id in domain_ids
    )
    if any(account.domain_ref is not None and account.domain_ref not in domain_ids for account in accounts):
        raise AcesPlanError("domain account references an unsupported domain")
    return domains
