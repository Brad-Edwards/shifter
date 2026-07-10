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

from aces_gcp_plan import build_aces_range_cell_plan
from aces_plan import AcesPlan, AcesPlanNode
from config import GCERangeCellConfig, GCERangeImageProfile, load_gce_range_cell_config
from gcp_guest_secrets import delete_aces_ssh_secret, ensure_aces_ssh_secret
from gcp_range_cell_clients import GCEClients, _build_clients
from gcp_range_cell_ops import _wait_for_operation
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
    _instance_output,
    _subnet_outputs,
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


def _default_secret_ops() -> AcesGceSecretOps:
    """Return the production ACES SSH-secret operation bindings."""
    return AcesGceSecretOps(ensure_ssh=ensure_aces_ssh_secret, delete_ssh=delete_aces_ssh_secret)


def _default_destroy_profile(_node: AcesPlanNode) -> GCERangeImageProfile:
    """Image resolver used for destroy: teardown deletes by name, so no image."""
    return GCERangeImageProfile()


def _ensure_aces_instance(
    plan: RangeCellPlan,
    clients: GCEClients,
    config: GCERangeCellConfig,
    instance: InstancePlan,
    secret_ops: AcesGceSecretOps,
) -> tuple[str, str, str]:
    """Create one ACES range instance with a provisioner-managed SSH + host key.

    Returns ``(ssh_secret_ref, ssh_public_key, host_public_key)``. The provisioner
    mints the guest's SSH host keypair, injects the private half via the startup
    script, and keeps the public half so the setup runner can seed known_hosts
    (StrictHostKeyChecking against a trusted side-channel key). On reconcile the
    guest already serves the injected host key, so it is recovered from metadata.
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
        ),
    )
    _wait_for_operation(plan, clients, operation, "zone")
    return secret_ref, public_key, host_public_key


def _provision_aces_resources(
    plan: RangeCellPlan,
    clients: GCEClients,
    config: GCERangeCellConfig,
    secret_ops: AcesGceSecretOps,
) -> list[ResourceDict]:
    """Create the network, subnets, firewalls, and instances for an ACES range."""
    if plan["manage_network"]:
        _ensure_network(plan, clients)
    for subnet in plan["subnets"]:
        _ensure_subnetwork(plan, clients, subnet)
    for firewall in plan["firewalls"]:
        _ensure_firewall(plan, clients, firewall)
    instance_outputs: list[ResourceDict] = []
    for instance in plan["instances"]:
        _ensure_address(plan, clients, instance)
        ssh_secret_ref, ssh_public_key, host_public_key = _ensure_aces_instance(
            plan, clients, config, instance, secret_ops
        )
        instance_outputs.append(
            _instance_output(
                plan,
                instance,
                ssh_secret_ref=ssh_secret_ref,
                rdp_password_secret_ref=None,
                ssh_public_key=ssh_public_key,
                host_public_key=host_public_key,
                config=config,
            )
        )
    return instance_outputs


def apply_aces_range_cell(
    request_uuid: str,
    range_id: int,
    aces_plan: AcesPlan,
    resolve_image: Callable[[AcesPlanNode], GCERangeImageProfile],
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: AcesGceSecretOps | None = None,
) -> ResourceDict:
    """Provision an ACES GCE range cell and return provisioner outputs."""
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    plan = build_aces_range_cell_plan(request_uuid, range_id, aces_plan, resolve_image, resolved_config)
    try:
        instance_outputs = _provision_aces_resources(plan, resolved_clients, resolved_config, resolved_secret_ops)
    except Exception:
        logger.exception("ACES GCE range-cell apply failed; attempting cleanup request_id=%s", request_uuid)
        destroy_aces_range_cell(
            request_uuid,
            range_id,
            aces_plan,
            config=resolved_config,
            clients=resolved_clients,
            secret_ops=resolved_secret_ops,
        )
        raise
    return {"subnets": _subnet_outputs(plan), "instances": instance_outputs}


def destroy_aces_range_cell(
    request_uuid: str,
    range_id: int,
    aces_plan: AcesPlan,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: AcesGceSecretOps | None = None,
) -> None:
    """Destroy every GCE resource owned by one ACES range cell."""
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
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
