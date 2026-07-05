"""Compute Engine range-cell backend for live-fire GCP ranges."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from config import GCERangeCellConfig, load_gce_range_cell_config
from gcp_guest_secrets import (
    delete_rdp_password_secret,
    delete_ssh_secret,
    ensure_rdp_password_secret,
    ensure_ssh_secret,
)
from gcp_range_cell_clients import GCEClients, GoogleExceptions, _build_clients
from gcp_range_cell_plan import (
    FirewallPlan,
    InstancePlan,
    RangeCellPlan,
    ResourceDict,
    ScenarioInstance,
    SubnetPlan,
    render_range_cell_plan,
)
from gcp_range_cell_resources import (
    address_resource,
    firewall_resource,
    instance_resource,
    network_resource,
    subnetwork_resource,
)
from log_redact import safe_log_fingerprint

logger = logging.getLogger(__name__)

_OPERATION_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class GCEGuestSecretOps:
    """Guest credential operations used by the GCE range-cell backend."""

    ensure_ssh: Callable[[int, ScenarioInstance], tuple[str, str]]
    ensure_rdp_password: Callable[[int, ScenarioInstance], tuple[str, str]]
    delete_ssh: Callable[[int, ScenarioInstance], None]
    delete_rdp_password: Callable[[int, ScenarioInstance], None]


def _default_secret_ops() -> GCEGuestSecretOps:
    """Return the production guest-secret operation bindings."""
    return GCEGuestSecretOps(
        ensure_ssh=ensure_ssh_secret,
        ensure_rdp_password=ensure_rdp_password_secret,
        delete_ssh=delete_ssh_secret,
        delete_rdp_password=delete_rdp_password_secret,
    )


def _operation_name(operation: object) -> str:
    """Extract a Compute operation name from SDK or dict responses."""
    if isinstance(operation, dict):
        return str(operation.get("name", ""))
    return str(getattr(operation, "name", "") or "")


def _get_operation_field(operation: object, name: str) -> object | None:
    """Read an operation field from SDK or dict responses."""
    if isinstance(operation, dict):
        return operation.get(name)
    return getattr(operation, name, None)


def _operation_error_messages(operation: object) -> list[str]:
    """Extract provider error messages from a completed operation."""
    error = _get_operation_field(operation, "error")
    if not error:
        return []
    entries = error.get("errors") if isinstance(error, dict) else _get_operation_field(error, "errors")
    if not isinstance(entries, list):
        return [str(error)]
    messages: list[str] = []
    for entry in entries:
        code = _get_operation_field(entry, "code")
        message = _get_operation_field(entry, "message")
        if code and message:
            messages.append(f"{code}: {message}")
        elif message:
            messages.append(str(message))
        else:
            messages.append(str(entry))
    return messages


def _raise_for_operation_errors(operation: object, *, operation_name: str, scope: str) -> None:
    """Raise when Compute reports errors on a completed operation."""
    errors = _operation_error_messages(operation)
    if errors:
        detail = "; ".join(errors)
        raise RuntimeError(f"GCE {scope} operation {operation_name or '<unknown>'} failed: {detail}")


def _wait_for_operation(plan: RangeCellPlan, clients: GCEClients, operation: object, scope: str) -> None:
    """Wait for a Compute operation and surface asynchronous failures."""
    if operation is None:
        return
    result_method = getattr(operation, "result", None)
    if callable(result_method):
        result = result_method(timeout=_OPERATION_TIMEOUT_SECONDS)
        _raise_for_operation_errors(result or operation, operation_name=_operation_name(operation), scope=scope)
        return

    operation_name = _operation_name(operation)
    if not operation_name:
        _raise_for_operation_errors(operation, operation_name="", scope=scope)
        return

    result = None
    if scope == "global":
        result = clients.global_operations.wait(project=plan["project_id"], operation=operation_name)
    elif scope == "region":
        result = clients.region_operations.wait(
            project=plan["project_id"], region=plan["region"], operation=operation_name
        )
    elif scope == "zone":
        result = clients.zone_operations.wait(project=plan["project_id"], zone=plan["zone"], operation=operation_name)
    _raise_for_operation_errors(result or operation, operation_name=operation_name, scope=scope)


def _get_or_none(
    callable_obj: Callable[..., object],
    exceptions: GoogleExceptions,
    **kwargs: object,
) -> object | None:
    """Return a Compute resource or None when the provider reports NotFound."""
    try:
        return callable_obj(**kwargs)
    except exceptions.NotFound:
        return None


def _ensure_network(plan: RangeCellPlan, clients: GCEClients) -> None:
    """Create the range VPC if it is missing."""
    name = plan["network"]["name"]
    existing = _get_or_none(clients.networks.get, clients.google_exceptions, project=plan["project_id"], network=name)
    if existing is not None:
        logger.info("GCE range network exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.networks.insert(project=plan["project_id"], network_resource=network_resource(plan))
    _wait_for_operation(plan, clients, operation, "global")


def _ensure_subnetwork(plan: RangeCellPlan, clients: GCEClients, subnet: SubnetPlan) -> None:
    """Create a range subnetwork if it is missing."""
    name = subnet["resource_name"]
    existing = _get_or_none(
        clients.subnetworks.get,
        clients.google_exceptions,
        project=plan["project_id"],
        region=plan["region"],
        subnetwork=name,
    )
    if existing is not None:
        logger.info("GCE range subnetwork exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.subnetworks.insert(
        project=plan["project_id"],
        region=plan["region"],
        subnetwork_resource=subnetwork_resource(plan, subnet),
    )
    _wait_for_operation(plan, clients, operation, "region")


def _ensure_firewall(plan: RangeCellPlan, clients: GCEClients, firewall: FirewallPlan) -> None:
    """Create one range firewall rule if it is missing."""
    name = firewall["name"]
    existing = _get_or_none(clients.firewalls.get, clients.google_exceptions, project=plan["project_id"], firewall=name)
    if existing is not None:
        logger.info("GCE range firewall exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.firewalls.insert(
        project=plan["project_id"], firewall_resource=firewall_resource(plan, firewall)
    )
    _wait_for_operation(plan, clients, operation, "global")


def _ensure_address(plan: RangeCellPlan, clients: GCEClients, instance: InstancePlan) -> None:
    """Reserve an internal address for one range instance."""
    name = instance["address_name"]
    existing = _get_or_none(
        clients.addresses.get,
        clients.google_exceptions,
        project=plan["project_id"],
        region=plan["region"],
        address=name,
    )
    if existing is not None:
        logger.info("GCE range address exists name_fp=%s", safe_log_fingerprint(name))
        return
    operation = clients.addresses.insert(
        project=plan["project_id"],
        region=plan["region"],
        address_resource=address_resource(plan, instance),
    )
    _wait_for_operation(plan, clients, operation, "region")


def _ensure_instance(
    plan: RangeCellPlan,
    clients: GCEClients,
    config: GCERangeCellConfig,
    instance: InstancePlan,
    secret_ops: GCEGuestSecretOps,
) -> tuple[str, str | None]:
    """Create one range instance and its guest credential secrets."""
    name = instance["resource_name"]
    existing = _get_or_none(
        clients.instances.get,
        clients.google_exceptions,
        project=plan["project_id"],
        zone=plan["zone"],
        instance=name,
    )
    secret_ref, public_key = secret_ops.ensure_ssh(plan["range_id"], instance["source"])
    rdp_password_secret_ref: str | None = None
    if instance["role"] != "dc":
        rdp_password_secret_ref, _password = secret_ops.ensure_rdp_password(plan["range_id"], instance["source"])
    if existing is not None:
        logger.info("GCE range instance exists name_fp=%s", safe_log_fingerprint(name))
        return secret_ref, rdp_password_secret_ref

    operation = clients.instances.insert(
        project=plan["project_id"],
        zone=plan["zone"],
        instance_resource=instance_resource(plan, instance, config, ssh_public_key=public_key),
    )
    _wait_for_operation(plan, clients, operation, "zone")
    return secret_ref, rdp_password_secret_ref


def _instance_output(
    plan: RangeCellPlan,
    instance: InstancePlan,
    *,
    ssh_secret_ref: str,
    rdp_password_secret_ref: str | None,
    config: GCERangeCellConfig,
) -> ResourceDict:
    """Render the provisioner output for one created instance."""
    output: ResourceDict = {
        "uuid": instance["uuid"],
        "name": instance["name"],
        "asset_type": "gce_vm",
        "role": instance["role"],
        "os": instance["os_type"],
        "subnet_name": instance["subnet_name"],
        "instance_id": instance["resource_name"],
        "private_ip": instance["private_ip"],
        "ssh_key_secret_arn": ssh_secret_ref,
        "ssh_username": instance["ssh_username"],
        "gcp_project_id": plan["project_id"],
        "gcp_region": plan["region"],
        "gcp_zone": plan["zone"],
        "gcp_network_name": plan["network"]["name"],
        "gcp_network_self_link": plan["network"]["self_link"],
        "gcp_subnetwork_name": instance["subnet_resource_name"],
        "gcp_subnetwork_self_link": instance["subnetwork_link"],
        "gcp_instance_name": instance["resource_name"],
        "gcp_address_name": instance["address_name"],
        "gcp_network_tags": instance["tags"],
        "gcp_service_account_email": config.service_account_email,
    }
    if rdp_password_secret_ref:
        output["rdp_password_secret_arn"] = rdp_password_secret_ref
        output["gcp_rdp_password_secret_ref"] = rdp_password_secret_ref
    return output


def _subnet_outputs(plan: RangeCellPlan) -> dict[str, ResourceDict]:
    """Render provisioner subnet outputs keyed by scenario subnet name."""
    return {
        subnet["name"]: {
            "uuid": subnet["uuid"],
            "subnet_id": subnet["resource_name"],
            "subnet_cidr": subnet["cidr"],
            "gcp_network_name": plan["network"]["name"],
            "gcp_network_self_link": plan["network"]["self_link"],
            "gcp_subnetwork_name": subnet["resource_name"],
            "gcp_subnetwork_self_link": subnet["self_link"],
            "gcp_region": plan["region"],
            "gcp_gateway_reserved": True,
            "gcp_instance_ip_assignments": subnet["ip_assignments"],
        }
        for subnet in plan["subnets"]
    }


def apply_range_cell(
    request_uuid: str,
    variables: ResourceDict,
    *,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: GCEGuestSecretOps | None = None,
    cleanup_range_cell: Callable[[str, ResourceDict | None], None] | None = None,
) -> ResourceDict:
    """Create or reconcile a live-fire GCE range cell and return provisioner outputs."""
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    plan = render_range_cell_plan(request_uuid, variables, resolved_config)
    instance_outputs: list[ResourceDict] = []
    try:
        _ensure_network(plan, resolved_clients)
        for subnet in plan["subnets"]:
            _ensure_subnetwork(plan, resolved_clients, subnet)
        for firewall in plan["firewalls"]:
            _ensure_firewall(plan, resolved_clients, firewall)
        for instance in plan["instances"]:
            _ensure_address(plan, resolved_clients, instance)
            ssh_secret_ref, rdp_password_secret_ref = _ensure_instance(
                plan,
                resolved_clients,
                resolved_config,
                instance,
                resolved_secret_ops,
            )
            instance_outputs.append(
                _instance_output(
                    plan,
                    instance,
                    ssh_secret_ref=ssh_secret_ref,
                    rdp_password_secret_ref=rdp_password_secret_ref,
                    config=resolved_config,
                )
            )
    except Exception:
        logger.exception("GCE range-cell apply failed; attempting cleanup request_id=%s", request_uuid)
        cleanup = cleanup_range_cell or (
            lambda cleanup_request_uuid, cleanup_variables: destroy_range_cell(
                cleanup_request_uuid,
                cleanup_variables,
                config=resolved_config,
                clients=resolved_clients,
                secret_ops=resolved_secret_ops,
            )
        )
        cleanup(request_uuid, variables)
        raise
    return {"subnets": _subnet_outputs(plan), "instances": instance_outputs}


def _delete_resource(
    plan: RangeCellPlan,
    clients: GCEClients,
    getter: Callable[..., object],
    deleter: Callable[..., object],
    scope: str,
    **kwargs: object,
) -> None:
    """Delete a Compute resource when it exists."""
    name = str(next(reversed(kwargs.values())))
    existing = _get_or_none(getter, clients.google_exceptions, **kwargs)
    if existing is None:
        return
    operation = deleter(**kwargs)
    _wait_for_operation(plan, clients, operation, scope)
    logger.info("Deleted GCE range resource name_fp=%s", safe_log_fingerprint(name))


def destroy_range_cell(
    request_uuid: str,
    variables: ResourceDict | None,
    *,
    config: GCERangeCellConfig | None = None,
    clients: GCEClients | None = None,
    secret_ops: GCEGuestSecretOps | None = None,
) -> None:
    """Destroy every GCE resource owned by one range cell."""
    if not variables:
        logger.info("No GCE range variables provided for request %s; nothing to destroy", request_uuid)
        return
    resolved_config = config or load_gce_range_cell_config()
    resolved_clients = clients or _build_clients()
    resolved_secret_ops = secret_ops or _default_secret_ops()
    plan = render_range_cell_plan(request_uuid, variables, resolved_config, require_images=False)

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
        resolved_secret_ops.delete_ssh(plan["range_id"], instance["source"])
        resolved_secret_ops.delete_rdp_password(plan["range_id"], instance["source"])

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

    _delete_resource(
        plan,
        resolved_clients,
        resolved_clients.networks.get,
        resolved_clients.networks.delete,
        "global",
        project=plan["project_id"],
        network=plan["network"]["name"],
    )
