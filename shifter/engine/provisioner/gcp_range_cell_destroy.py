"""Teardown of every GCE resource owned by one range cell."""

from __future__ import annotations

import logging

from config import GCERangeCellConfig, load_gce_range_cell_config
from gcp_range_cell_clients import GCEClients, _build_clients
from gcp_range_cell_credentials import (
    GCEGuestSecretOps,
    GCEVertexCredentialOps,
    _default_secret_ops,
    _default_vertex_ops,
)
from gcp_range_cell_ops import _delete_resource, _get_or_none, _wait_for_operation
from gcp_range_cell_plan import render_range_cell_plan
from gcp_range_cell_types import RangeCellPlan, ResourceDict
from provisioner_db import get_range_data_by_request_id

logger = logging.getLogger(__name__)


def _destroy_vpn_gateway(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Delete the request-owned OpenVPN gateway instance and its address."""
    gateway = plan.get("vpn_gateway")
    if gateway is None:
        return
    _delete_resource(
        plan,
        clients,
        clients.instances.get,
        clients.instances.delete,
        "zone",
        project=plan["project_id"],
        zone=plan["zone"],
        instance=gateway["resource_name"],
    )
    _delete_resource(
        plan,
        clients,
        clients.addresses.get,
        clients.addresses.delete,
        "region",
        project=plan["project_id"],
        region=plan["region"],
        address=gateway["address_name"],
    )


def _destroy_instances(plan: RangeCellPlan, clients: GCEClients, secret_ops: GCEGuestSecretOps) -> None:
    """Delete range instances, their addresses, and their guest secrets."""
    for instance in reversed(plan["instances"]):
        existing = _get_or_none(
            clients.instances.get,
            clients.google_exceptions,
            project=plan["project_id"],
            zone=plan["zone"],
            instance=instance["resource_name"],
        )
        if existing is not None:
            for disk in getattr(existing, "disks", None) or []:
                if bool(getattr(disk, "auto_delete", False)):
                    continue
                device_name = str(getattr(disk, "device_name", "") or "")
                if not device_name:
                    raise RuntimeError("GCE range instance has an attached disk without a device name")
                operation = clients.instances.set_disk_auto_delete(
                    project=plan["project_id"],
                    zone=plan["zone"],
                    instance=instance["resource_name"],
                    device_name=device_name,
                    auto_delete=True,
                )
                _wait_for_operation(plan, clients, operation, "zone")
        _delete_resource(
            plan,
            clients,
            clients.instances.get,
            clients.instances.delete,
            "zone",
            project=plan["project_id"],
            zone=plan["zone"],
            instance=instance["resource_name"],
        )
        _delete_resource(
            plan,
            clients,
            clients.addresses.get,
            clients.addresses.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            address=instance["address_name"],
        )
        secret_ops.delete_ssh(plan["range_id"], instance["source"])
        secret_ops.delete_participant_ssh(plan["range_id"], instance["source"])
        secret_ops.delete_rdp_password(plan["range_id"], instance["source"])


def _destroy_network_resources(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Delete firewalls, subnets, and (when range-owned) the VPC itself."""
    for firewall in reversed(plan["firewalls"]):
        _delete_resource(
            plan,
            clients,
            clients.firewalls.get,
            clients.firewalls.delete,
            "global",
            project=plan["project_id"],
            firewall=firewall["name"],
        )

    for subnet in reversed(plan["subnets"]):
        _delete_resource(
            plan,
            clients,
            clients.subnetworks.get,
            clients.subnetworks.delete,
            "region",
            project=plan["project_id"],
            region=plan["region"],
            subnetwork=subnet["resource_name"],
        )

    # In shared-vpc mode the range VPC is the pre-existing, platform-peered
    # network and must never be deleted; only per-range subnets/firewalls are torn
    # down above.
    if plan["manage_network"]:
        _delete_resource(
            plan,
            clients,
            clients.networks.get,
            clients.networks.delete,
            "global",
            project=plan["project_id"],
            network=plan["network"]["name"],
        )


def destroy_range_cell(
    request_uuid: str,
    variables: ResourceDict | None,
    *,
    backend: str | None = None,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: GCEGuestSecretOps | None = None,
    vertex_ops: GCEVertexCredentialOps | None = None,
) -> None:
    """Destroy every GCE resource owned by one range cell."""
    if not variables:
        logger.info("No GCE range variables provided for request %s; nothing to destroy", request_uuid)
        return
    resolved_config = config or load_gce_range_cell_config(backend=backend)
    # The gateway SA email is unused for teardown (resources are deleted by name),
    # but the range's reserved pool slot (ADR-008-R7) is read so the plan renders
    # consistently with provision. The row exists while the range is DESTROYING.
    range_data = get_range_data_by_request_id(request_uuid)
    plan = render_range_cell_plan(
        request_uuid,
        variables,
        resolved_config,
        require_images=False,
        vpn_gateway_pool_slot=range_data.get("vpn_gateway_pool_slot"),
        range_host_pool_slot=(
            int(range_data["subnet_index"]) - 1 if range_data.get("subnet_index") is not None else None
        ),
    )
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    resolved_vertex_ops = vertex_ops or _default_vertex_ops()

    # Delete the per-range Vertex agent key first; it is independent of the
    # Compute resources and idempotent, so it converges even on repeated destroy.
    resolved_vertex_ops.delete(plan["range_id"], plan["project_id"])

    _destroy_vpn_gateway(plan, resolved_clients)
    _destroy_instances(plan, resolved_clients, resolved_secret_ops)
    _destroy_network_resources(plan, resolved_clients)


__all__ = ["destroy_range_cell"]
