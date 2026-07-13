"""ACES-native GCE range-cell provisioning orchestration (ADR-031, ADR-032).

The ACES counterpart of ``gcp_range_cells.apply_range_cell``/``destroy_range_cell``.
It builds the neutral ``RangeCellPlan`` from a parsed serialized ACES plan
(:func:`aces_gcp_plan.build_aces_range_cell_plan`) and realizes it by reusing the
provenance-neutral GCE apply primitives (``_ensure_network``/``_ensure_subnetwork``/
``_ensure_firewall``/``_ensure_address``, ``_wait_for_operation``, ``GCEClients``,
the resource renderers, and the provisioner-issued host key).

It deliberately does NOT reuse the cyberscript ``_ensure_instance``/
``_provision_range_resources``: those branch on ``role == "dc"``, mint participant
SSH/RDP secrets keyed on a scenario ``instance["source"]``, and manage per-range
Vertex agent credentials -- all scenario/participant concerns. The ACES path is
provisioning-only: it mints one provisioner-managed SSH key per instance
(``ensure_aces_ssh_secret``) for range reachability, installs the injected host
key, and creates the guest. Participant access and scenario setup are later
participant-runtime concerns, not part of provisioning realization.

Destroy is reconstructive: the serialized plan yields deterministic resource
names, so teardown rebuilds the plan (with a default image profile, since only
names are needed) and deletes every owned resource -- no persisted output is
required.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass

from aces_account_credentials import (
    AcesAccountCredentialOps,
    default_account_credential_ops,
    delete_instance_account_credentials,
    install_instance_account_credentials,
)
from aces_gcp_composition import node_bootstrap_script
from aces_gcp_plan import AcesGcePlanError, build_aces_range_cell_plan
from aces_plan import AcesPlan, AcesPlanAccount, AcesPlanNode
from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from gcp_guest_secrets import delete_aces_ssh_secret, ensure_aces_ssh_secret
from gcp_range_cell_clients import GCEClients, _build_clients
from gcp_range_cell_ops import _wait_for_operation
from gcp_range_cell_outputs import InstanceCredentials, instance_output, subnet_outputs
from gcp_range_cell_plan import InstancePlan, RangeCellPlan, ResourceDict
from gcp_range_cell_resources import instance_resource
from gcp_range_cells import (
    _delete_resource,
    _ensure_address,
    _ensure_firewall,
    _ensure_network,
    _ensure_subnetwork,
    _get_or_none,
    _host_public_key_from_instance,
)
from utils.crypto import generate_ssh_host_keypair

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcesGceSecretOps:
    """Provisioner-managed SSH secret operations for the ACES range-cell backend.

    Keyed on ``(range_id, instance_key)`` -- the ACES instance key (node address +
    count index), never a cyberscript ``ScenarioInstance``. Injectable so tests
    exercise the orchestration without touching Secret Manager.
    """

    ensure_ssh: Callable[[int, str], tuple[str, str]]
    delete_ssh: Callable[[int, str], None]


@dataclass(frozen=True)
class AcesGceApplyOptions:
    """Optional infrastructure and credential bindings for an ACES apply."""

    config: GCERangeCellConfig | None = None
    clients: GCEClients | None = None
    secret_ops: AcesGceSecretOps | None = None
    account_secret_ops: AcesAccountCredentialOps | None = None
    credential_installer: Callable[..., None] = install_instance_account_credentials


@dataclass(frozen=True)
class _AcesGceApplyRuntime:
    """Resolved non-optional bindings shared by ACES resource realization."""

    config: GCERangeCellConfig
    clients: GCEClients
    secret_ops: AcesGceSecretOps
    account_secret_ops: AcesAccountCredentialOps
    credential_installer: Callable[..., None]


def _default_secret_ops() -> AcesGceSecretOps:
    """Return the production ACES SSH-secret operation bindings."""
    return AcesGceSecretOps(ensure_ssh=ensure_aces_ssh_secret, delete_ssh=delete_aces_ssh_secret)


def _default_destroy_profile(_node: AcesPlanNode) -> GCERangeImageProfile:
    """Image resolver used for destroy: teardown deletes by name, so no image."""
    return GCERangeImageProfile()


def _assert_composition_targets_resolve(aces_plan: AcesPlan) -> None:
    """Fail closed if any content/feature/account placement targets an unknown node."""
    node_addresses = {node.address for node in aces_plan.nodes}
    placements = (
        [(c.target_address, "content", c.name) for c in aces_plan.content]
        + [(a.target_address, "account", a.username) for a in aces_plan.accounts]
        + [(f.target_address, "feature", f.name) for f in aces_plan.features]
    )
    for target, kind, name in placements:
        if target not in node_addresses:
            raise AcesGcePlanError(f"{kind} placement {name!r} targets node {target!r} not present in this plan")


def _node_address_of(instance: InstancePlan) -> str:
    """Return the ACES node address an instance belongs to (uuid = ``address#index``)."""
    return str(instance["uuid"]).rsplit("#", 1)[0]


def _ensure_aces_instance(
    plan: RangeCellPlan,
    clients: GCEClients,
    config: GCERangeCellConfig,
    instance: InstancePlan,
    secret_ops: AcesGceSecretOps,
    bootstrap_by_node: dict[str, str],
) -> tuple[str, str, str]:
    """Create one ACES range instance with a provisioner-managed SSH + host key.

    Returns ``(ssh_secret_ref, ssh_public_key, host_public_key)``. The provisioner
    mints the guest's SSH host keypair, injects the private half via the startup
    script, and keeps the public half so the setup runner can seed known_hosts
    (StrictHostKeyChecking against a trusted side-channel key). The node's
    composition bootstrap (content/features/accounts) is appended to that startup
    script. On reconcile the guest already serves the injected host key, so it is
    recovered from metadata.
    """
    name = instance["resource_name"]
    existing = _get_or_none(
        clients.instances.get,
        clients.google_exceptions,
        project=plan["project_id"],
        zone=plan["zone"],
        instance=name,
    )
    secret_ref, public_key = secret_ops.ensure_ssh(plan["range_id"], instance["uuid"])
    if existing is not None:
        return secret_ref, public_key, _host_public_key_from_instance(existing)

    host_private_key, host_public_key = generate_ssh_host_keypair()
    host_private_key_b64 = base64.b64encode(host_private_key.encode()).decode("ascii")
    operation = clients.instances.insert(
        project=plan["project_id"],
        zone=plan["zone"],
        instance_resource=instance_resource(
            plan,
            instance,
            config,
            ssh_public_key=public_key,
            host_private_key_b64=host_private_key_b64,
            host_public_key=host_public_key,
            composition_script=bootstrap_by_node.get(_node_address_of(instance), ""),
        ),
    )
    _wait_for_operation(plan, clients, operation, "zone")
    return secret_ref, public_key, host_public_key


def _provision_aces_resources(
    plan: RangeCellPlan,
    runtime: _AcesGceApplyRuntime,
    bootstrap_by_node: dict[str, str],
    accounts_by_node: dict[str, tuple[AcesPlanAccount, ...]],
) -> list[ResourceDict]:
    """Create the network, subnets, firewalls, and instances for an ACES range."""
    if plan["manage_network"]:
        _ensure_network(plan, runtime.clients)
    for subnet in plan["subnets"]:
        _ensure_subnetwork(plan, runtime.clients, subnet)
    for firewall in plan["firewalls"]:
        _ensure_firewall(plan, runtime.clients, firewall)
    instance_outputs: list[ResourceDict] = []
    for instance in plan["instances"]:
        _ensure_address(plan, runtime.clients, instance)
        ssh_secret_ref, ssh_public_key, host_public_key = _ensure_aces_instance(
            plan,
            runtime.clients,
            runtime.config,
            instance,
            runtime.secret_ops,
            bootstrap_by_node,
        )
        output = instance_output(
            plan,
            instance,
            InstanceCredentials(
                host_ssh_secret_ref=ssh_secret_ref,
                participant_ssh_secret_ref=None,
                rdp_password_secret_ref=None,
                ssh_public_key=ssh_public_key,
                host_public_key=host_public_key,
            ),
            runtime.config,
        )
        accounts = accounts_by_node.get(_node_address_of(instance), ())
        if accounts:
            runtime.credential_installer(
                range_id=plan["range_id"],
                instance_key=instance["uuid"],
                platform=instance["os_type"],
                instance_output=output,
                accounts=accounts,
                secret_ops=runtime.account_secret_ops,
            )
        instance_outputs.append(output)
    return instance_outputs


def apply_aces_range_cell(
    request_uuid: str,
    range_id: int,
    aces_plan: AcesPlan,
    resolve_image: Callable[[AcesPlanNode], GCERangeImageProfile],
    options: AcesGceApplyOptions | None = None,
) -> ResourceDict:
    """Provision an ACES GCE range cell and return provisioner outputs."""
    options = options or AcesGceApplyOptions()
    runtime = _AcesGceApplyRuntime(
        config=options.config or load_gce_range_cell_config(),
        clients=options.clients or _build_clients(),
        secret_ops=options.secret_ops or _default_secret_ops(),
        account_secret_ops=options.account_secret_ops or default_account_credential_ops(),
        credential_installer=options.credential_installer,
    )
    _assert_composition_targets_resolve(aces_plan)
    plan = build_aces_range_cell_plan(request_uuid, range_id, aces_plan, resolve_image, runtime.config)
    bootstrap_by_node = {
        node.address: script for node in aces_plan.nodes if (script := node_bootstrap_script(node, aces_plan))
    }
    accounts_by_node = {
        node.address: tuple(account for account in aces_plan.accounts if account.target_address == node.address)
        for node in aces_plan.nodes
    }
    try:
        instance_outputs = _provision_aces_resources(
            plan,
            runtime,
            bootstrap_by_node,
            accounts_by_node,
        )
    except Exception:
        logger.exception("ACES GCE range-cell apply failed; attempting cleanup request_id=%s", request_uuid)
        destroy_aces_range_cell(
            request_uuid,
            range_id,
            aces_plan,
            config=runtime.config,
            clients=runtime.clients,
            secret_ops=runtime.secret_ops,
            account_secret_ops=runtime.account_secret_ops,
        )
        raise
    return {"subnets": subnet_outputs(plan), "instances": instance_outputs}


def destroy_aces_range_cell(
    request_uuid: str,
    range_id: int,
    aces_plan: AcesPlan,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: AcesGceSecretOps | None = None,
    *,
    account_secret_ops: AcesAccountCredentialOps | None = None,
) -> None:
    """Destroy every GCE resource owned by one ACES range cell."""
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    resolved_account_secret_ops = account_secret_ops or default_account_credential_ops()
    plan = build_aces_range_cell_plan(request_uuid, range_id, aces_plan, _default_destroy_profile, resolved_config)

    for instance in reversed(plan["instances"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.instances.get,
            resolved_clients.instances.delete,
            "zone",
            project=plan["project_id"],
            zone=plan["zone"],
            instance=instance["resource_name"],
        )
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.addresses.get,
            resolved_clients.addresses.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            address=instance["address_name"],
        )
        resolved_secret_ops.delete_ssh(plan["range_id"], instance["uuid"])
        accounts = tuple(
            account for account in aces_plan.accounts if account.target_address == _node_address_of(instance)
        )
        delete_instance_account_credentials(plan["range_id"], instance["uuid"], accounts, resolved_account_secret_ops)

    for firewall in reversed(plan["firewalls"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.firewalls.get,
            resolved_clients.firewalls.delete,
            "global",
            project=plan["project_id"],
            firewall=firewall["name"],
        )

    for subnet in reversed(plan["subnets"]):
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.subnetworks.get,
            resolved_clients.subnetworks.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            subnetwork=subnet["resource_name"],
        )

    # In shared-vpc mode the range VPC is the pre-existing, platform-peered network
    # and must never be deleted; only per-range subnets/firewalls are torn down.
    if plan["manage_network"]:
        _delete_resource(
            plan,
            resolved_clients,
            resolved_clients.networks.get,
            resolved_clients.networks.delete,
            "global",
            project=plan["project_id"],
            network=plan["network"]["name"],
        )
