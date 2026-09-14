"""RAES-free validation of the versioned plan's semantic metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from raes_plan_types import RaesPlanError

CONTRACT_VERSION = "raes-provisioning-plan-v2"
PRODUCER_VERSION = "3.5.0"
_LEGACY_PAIR = ("raes-provisioning-plan-v1", "2.0.0")


def prepare_envelope(envelope: Mapping[str, Any], *, cleanup_only: bool) -> Mapping[str, Any]:
    """Select a reviewed version before reading payloads; retain old cleanup."""
    pair = (envelope.get("contract_version"), envelope.get("raes_version"))
    if cleanup_only and pair == _LEGACY_PAIR:
        return _legacy_cleanup(envelope)
    if pair != (CONTRACT_VERSION, PRODUCER_VERSION):
        raise RaesPlanError("unsupported contract_version or raes_version")
    _validate_collection_fields(envelope)
    _validate_operation_id(envelope.get("operation_id"))
    carrier = _validate_carrier_identity(envelope.get("realization_envelope"))
    selected_carrier = envelope.get("backend_realization_envelope")
    if selected_carrier is not None:
        _validate_selected_carrier(selected_carrier, carrier)
    _validate_authority(envelope)
    _validate_constraints(envelope)
    _validate_operations(envelope)
    return envelope


def _validate_collection_fields(envelope: Mapping[str, Any]) -> None:
    for field in ("operations", "realization_authority", "realization_constraints"):
        value = envelope.get(field, [])
        if not isinstance(value, list) or len(value) > 65536 or any(not isinstance(item, dict) for item in value):
            raise RaesPlanError(f"{field} must be a list of objects")


def _validate_operation_id(identity: object) -> None:
    if identity is not None and (not isinstance(identity, str) or not identity.strip() or len(identity) > 128):
        raise RaesPlanError("operation_id must be a bounded non-empty string")


def _validate_carrier_identity(carrier: object) -> Mapping[str, Any] | None:
    if carrier is None:
        return None
    required = {"contract_id", "envelope_id", "schema_version", "digest", "configuration_digest"}
    if not isinstance(carrier, dict) or set(carrier) != required:
        raise RaesPlanError("realization_envelope has an invalid identity")
    if any(not isinstance(value, str) or not value or len(value) > 256 for value in carrier.values()):
        raise RaesPlanError("realization_envelope has an invalid identity")
    if carrier["contract_id"] != "realization-envelope-v1" or carrier["schema_version"] != "realization-envelope/v1":
        raise RaesPlanError("realization_envelope has an unsupported version")
    if any(re.fullmatch(r"sha256:[a-f0-9]{64}", carrier[key]) is None for key in ("digest", "configuration_digest")):
        raise RaesPlanError("realization_envelope has an invalid digest")
    return carrier


def _validate_selected_carrier(value: object, identity: object) -> None:
    """Bound the runtime-selected carrier without interpreting RAES semantics."""
    if not isinstance(value, Mapping) or not isinstance(identity, Mapping):
        raise RaesPlanError("backend_realization_envelope requires a plan identity")
    required = {"schema_version", "contract_id", "id", "expression", "configuration", "concerns", "digest"}
    if set(value) != required:
        raise RaesPlanError("backend_realization_envelope has invalid fields")
    if (
        value.get("schema_version") != "realization-envelope/v1"
        or value.get("contract_id") != "realization-envelope-v1"
    ):
        raise RaesPlanError("backend_realization_envelope has an unsupported version")
    if value.get("id") != identity.get("envelope_id") or value.get("digest") != identity.get("digest"):
        raise RaesPlanError("backend_realization_envelope diverges from the plan identity")
    configuration = value.get("configuration")
    if not isinstance(configuration, Mapping) or configuration.get("configuration_digest") != identity.get(
        "configuration_digest"
    ):
        raise RaesPlanError("backend_realization_envelope has an invalid configuration identity")
    if not isinstance(value.get("expression"), Mapping) or not isinstance(value.get("concerns"), list):
        raise RaesPlanError("backend_realization_envelope has an invalid shape")


def _validate_authority(envelope: Mapping[str, Any]) -> None:
    """Read the supported authority ledger without accepting malformed presence."""
    required = {"address", "field_path", "domain", "requirement_kind", "payload_pointer", "mode", "source"}
    optional = {"provenance", "governing_scope", "bounds", "verification_scope", "required_observation_strength"}
    seen: set[tuple[str, str]] = set()
    for row in envelope.get("realization_authority", []):
        identity = _validate_authority_row(row, envelope.get("resources", {}), required, optional)
        if identity in seen:
            raise RaesPlanError("realization_authority has an invalid resource identity")
        seen.add(identity)


def _validate_authority_row(
    row: Mapping[str, Any], resources: object, required: set[str], optional: set[str]
) -> tuple[str, str]:
    if not required <= set(row) or set(row) - required - optional:
        raise RaesPlanError("realization_authority has invalid fields")
    if any(not isinstance(row[key], str) or not row[key] or len(row[key]) > 1024 for key in required):
        raise RaesPlanError("realization_authority has an invalid identity")
    identity = (row["address"], row["field_path"])
    if not isinstance(resources, Mapping) or row["address"] not in resources:
        raise RaesPlanError("realization_authority has an invalid resource identity")
    if row["mode"] not in {"closed", "open", "exact", "constrained"}:
        raise RaesPlanError("realization_authority has an unsupported posture")
    if row["source"] not in {
        "authored-leaf",
        "authored-scope",
        "apparatus-default",
        "legacy-default",
        "processor-derived",
    }:
        raise RaesPlanError("realization_authority has an unsupported source")
    if re.fullmatch(r"(?:/(?:[^~/]|~[01])*)+", row["payload_pointer"]) is None:
        raise RaesPlanError("realization_authority has an invalid payload pointer")
    if row.get("bounds", []) != []:
        raise RaesPlanError("realization_authority bounds require unsupported constrained realization")
    for field in optional - {"bounds"}:
        value = row.get(field)
        if value is not None and (not isinstance(value, str) or not value or len(value) > 1024):
            raise RaesPlanError("realization_authority has malformed optional metadata")
    return identity


def _validate_constraints(envelope: Mapping[str, Any]) -> None:
    """This VM backend cannot approximate an authored substrate constraint."""
    seen: set[str] = set()
    for constraint in envelope.get("realization_constraints", []):
        required = {"address", "field_path", "concern", "posture", "value_domain", "governing_scope", "provenance"}
        if set(constraint) != required or constraint.get("concern") != "compute-substrate":
            raise RaesPlanError("realization_constraints contain an unsupported constraint")
        address = constraint["address"]
        resources = envelope.get("resources", {})
        if (
            not isinstance(address, str)
            or address in seen
            or not isinstance(resources, Mapping)
            or address not in resources
        ):
            raise RaesPlanError("realization_constraints have an invalid resource identity")
        seen.add(address)
        domain = constraint["value_domain"]
        posture = constraint["posture"]
        if posture == "open" and domain is None:
            continue
        if not isinstance(domain, dict):
            raise RaesPlanError("realization_constraints require a supported substrate domain")
        if posture == "exact" and domain == {"kind": "exact", "value": "virtual-machine"}:
            continue
        if posture == "constrained" and set(domain) == {"kind", "values"} and domain["kind"] == "enum":
            values = domain["values"]
            if (
                isinstance(values, list)
                and all(isinstance(value, str) for value in values)
                and "virtual-machine" in values
            ):
                continue
        raise RaesPlanError("realization_constraints do not permit the virtual-machine substrate")


def _validate_operations(envelope: Mapping[str, Any]) -> None:
    """Handle validate operations."""
    resources = envelope.get("resources", {})
    seen: set[str] = set()
    for operation in envelope.get("operations", []):
        address = _validate_operation(operation, resources)
        if address in seen:
            raise RaesPlanError("operations contain duplicate resource identities")
        seen.add(address)


def _validate_operation(operation: Mapping[str, Any], resources: object) -> str:
    address = operation.get("address")
    if not isinstance(address, str) or not isinstance(resources, Mapping) or address not in resources:
        raise RaesPlanError("operations reference an unknown resource")
    resource = resources[address]
    if not isinstance(resource, Mapping) or operation.get("payload") != resource.get("payload"):
        raise RaesPlanError("operations diverge from resource payloads")
    if operation.get("resource_type") != resource.get("resource_type"):
        raise RaesPlanError("operations diverge from resource types")
    if operation.get("action") not in {"create", "unchanged"}:
        raise RaesPlanError("operations require unsupported incremental realization")
    refresh = operation.get("refresh_dependencies", [])
    ordering = operation.get("ordering_dependencies", [])
    if not isinstance(refresh, list) or not isinstance(ordering, list):
        raise RaesPlanError("operations dependencies must be address lists")
    if any(not isinstance(item, str) for item in (*refresh, *ordering)):
        raise RaesPlanError("operations dependencies must be address lists")
    # On a fresh create, each dependent is realized after its prerequisites.
    # No incremental reapply is supported by this reader.
    if not set(refresh) <= set(ordering) or refresh != resource.get("refresh_dependencies", []):
        raise RaesPlanError("operations refresh_dependencies require fresh-create ordering")
    if ordering != resource.get("ordering_dependencies", []) or any(item not in resources for item in ordering):
        raise RaesPlanError("operations ordering_dependencies diverge from the complete plan")
    return address


def _legacy_cleanup(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    """Normalize only the old cleanup view; persisted bytes and IDs stay intact."""
    result = deepcopy(dict(envelope))
    resources = result.get("resources")
    if not isinstance(resources, dict):
        raise RaesPlanError("resources must be an object")
    for resource in resources.values():
        if not isinstance(resource, dict) or not isinstance(resource.get("payload"), dict):
            continue
        payload = resource["payload"]
        address = resource.get("address")
        if not payload.get("name") and not payload.get("node_name") and isinstance(address, str):
            payload["name"] = address.rsplit(".", 1)[-1]
        spec = payload.get("spec")
        if (
            resource.get("resource_type") == "account-placement"
            and isinstance(spec, dict)
            and spec.get("auth_method") == "publickey"
        ):
            spec["auth_method"] = "key"
    return result
