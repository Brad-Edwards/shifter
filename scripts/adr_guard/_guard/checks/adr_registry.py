"""ADR registry integrity and interface-contract checks."""
from __future__ import annotations

import json
from pathlib import Path

from .._common import (
    Violation,
    _load_json_yaml,
    load_adr_exceptions,
    validate_adr_exceptions,
)


REQUIRED_ADR_KEYS = {
    "id",
    "title",
    "status",
    "scope",
    "decision",
    "rules",
    "exceptions",
    "enforcement",
    "evidence",
}
REQUIRED_INTERFACE_CONTRACTS = {"ADR-039": "range-substrate/v1"}
RANGE_SUBSTRATE_OPERATIONS = frozenset({"provision", "destroy", "pause", "resume"})
RANGE_SUBSTRATE_RESOURCES = frozenset({"network", "instance", "ngfw", "remote-access"})
RANGE_SUBSTRATE_INITIAL_ADAPTERS = frozenset({"aws-terraform", "gcp-gdc"})
RANGE_SUBSTRATE_DEFERRED_ADAPTERS = frozenset({"azure"})
RANGE_SUBSTRATE_ISSUE_REFERENCES = frozenset({"283", "478", "265", "277"})


def load_adr_registry(repo_root: Path) -> list[dict]:
    """Load and validate the ADR registry shape."""
    path = repo_root / "docs" / "adr" / "index.yaml"
    data = _load_json_yaml(path)
    if not isinstance(data, list):
        raise ValueError("docs/adr/index.yaml must contain a top-level list")
    return data


def _registry_violation(path: str, message: str) -> Violation:
    """Shorthand: build an adr-registry / ADR-REGISTRY Violation at `path`."""
    return Violation("adr-registry", "ADR-REGISTRY", path, message)


def _validate_exact_string_members(
    value: object,
    expected: frozenset[str],
    field: str,
) -> list[str]:
    """Validate one closed interface-contract string collection."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return [f"{field} must be a list of strings"]
    if len(value) != len(set(value)):
        return [f"{field} must not contain duplicates"]
    actual = set(value)
    if actual != expected:
        return [
            f"{field} must contain exactly {sorted(expected)}; got {sorted(actual)}"
        ]
    return []


def validate_interface_contract(contract: object, adr_id: str) -> list[str]:
    """Validate executable invariants declared by a typed ADR interface contract."""
    if not isinstance(contract, dict):
        return [f"{adr_id} interface_contract must be an object"]

    expected_kind = REQUIRED_INTERFACE_CONTRACTS.get(adr_id)
    kind = contract.get("kind")
    if expected_kind is not None and kind != expected_kind:
        return [f"{adr_id} interface_contract kind must be {expected_kind!r}"]
    if kind != "range-substrate/v1":
        return [f"{adr_id} interface_contract has unsupported kind {kind!r}"]

    errors: list[str] = []
    errors.extend(
        _validate_exact_string_members(
            contract.get("operations"),
            RANGE_SUBSTRATE_OPERATIONS,
            f"{adr_id} interface_contract.operations",
        )
    )
    errors.extend(
        _validate_exact_string_members(
            contract.get("resources"),
            RANGE_SUBSTRATE_RESOURCES,
            f"{adr_id} interface_contract.resources",
        )
    )

    conformance = contract.get("conformance")
    if not isinstance(conformance, dict):
        errors.append(f"{adr_id} interface_contract.conformance must be an object")
    else:
        for obligation in ("shared_black_box_suite", "real_provider_promotion_evidence"):
            if conformance.get(obligation) is not True:
                errors.append(
                    f"{adr_id} interface_contract.conformance.{obligation} must be true"
                )

    adapters = contract.get("adapters")
    if not isinstance(adapters, dict):
        errors.append(f"{adr_id} interface_contract.adapters must be an object")
    else:
        errors.extend(
            _validate_exact_string_members(
                adapters.get("initial"),
                RANGE_SUBSTRATE_INITIAL_ADAPTERS,
                f"{adr_id} interface_contract.adapters.initial",
            )
        )
        errors.extend(
            _validate_exact_string_members(
                adapters.get("deferred"),
                RANGE_SUBSTRATE_DEFERRED_ADAPTERS,
                f"{adr_id} interface_contract.adapters.deferred",
            )
        )

    references = contract.get("issue_references")
    if not isinstance(references, dict):
        errors.append(f"{adr_id} interface_contract.issue_references must be an object")
        return errors
    actual_references = set(references)
    if actual_references != RANGE_SUBSTRATE_ISSUE_REFERENCES:
        errors.append(
            f"{adr_id} interface_contract.issue_references must contain exactly "
            f"{sorted(RANGE_SUBSTRATE_ISSUE_REFERENCES)}; got {sorted(actual_references)}"
        )
    for reference, mapping in references.items():
        if not isinstance(mapping, dict):
            errors.append(f"{adr_id} issue reference {reference} mapping must be an object")
            continue
        mapping_fields = set(mapping)
        if mapping_fields == {"disposition"} and mapping["disposition"] == "out-of-scope":
            continue
        operations = mapping.get("operations")
        if (
            mapping_fields != {"operations"}
            or not isinstance(operations, list)
            or not operations
            or not all(isinstance(operation, str) for operation in operations)
            or not set(operations).issubset(RANGE_SUBSTRATE_OPERATIONS)
            or len(operations) != len(set(operations))
        ):
            errors.append(
                f"{adr_id} issue reference {reference} must exclusively map to a "
                "non-empty, duplicate-free list of declared operations or have only "
                "disposition 'out-of-scope'"
            )
    return errors


def _check_adr_entry(
    entry: dict,
    adr_ids: set[str],
    rule_ids: set[str],
    violations: list[Violation],
) -> None:
    """Validate one registry entry; append any per-entry violations.

    Also mutates `adr_ids` / `rule_ids` with the names this entry contributes
    so later entries can detect duplicates.
    """
    missing = REQUIRED_ADR_KEYS - set(entry)
    if missing:
        violations.append(
            _registry_violation(
                "docs/adr/index.yaml",
                f"ADR entry {entry.get('id', '<missing-id>')} is missing keys: {sorted(missing)}",
            )
        )
        return

    adr_id = entry["id"]
    if adr_id in adr_ids:
        violations.append(_registry_violation("docs/adr/index.yaml", f"Duplicate ADR id: {adr_id}"))
    adr_ids.add(adr_id)

    interface_contract = entry.get("interface_contract")
    if adr_id in REQUIRED_INTERFACE_CONTRACTS and interface_contract is None:
        violations.append(
            _registry_violation(
                "docs/adr/index.yaml",
                f"{adr_id} must define interface_contract kind "
                f"{REQUIRED_INTERFACE_CONTRACTS[adr_id]!r}",
            )
        )
    elif interface_contract is not None:
        for error in validate_interface_contract(interface_contract, adr_id):
            violations.append(_registry_violation("docs/adr/index.yaml", error))

    rules = entry.get("rules", [])
    if not isinstance(rules, list):
        violations.append(
            _registry_violation("docs/adr/index.yaml", f"{adr_id} rules must be a list")
        )
        return

    for rule in rules:
        rule_id = rule.get("id")
        if not rule_id:
            violations.append(
                _registry_violation(
                    "docs/adr/index.yaml", f"{adr_id} has a rule without an id"
                )
            )
            continue
        if rule_id in rule_ids:
            violations.append(
                _registry_violation("docs/adr/index.yaml", f"Duplicate rule id: {rule_id}")
            )
        rule_ids.add(rule_id)


def check_adr_registry(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Validate the ADR registry and exception references."""
    violations: list[Violation] = []

    try:
        registry = load_adr_registry(repo_root)
        exceptions = load_adr_exceptions(repo_root)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        return [Violation("adr-registry", "ADR-REGISTRY", "docs/adr", str(err))]

    for error in validate_adr_exceptions(exceptions):
        violations.append(_registry_violation("docs/adr/exceptions.yaml", error))

    adr_ids: set[str] = set()
    rule_ids: set[str] = set()
    for entry in registry:
        _check_adr_entry(entry, adr_ids, rule_ids, violations)

    for exception in exceptions:
        rule_id = exception.get("rule_id")
        if not rule_id or rule_id not in rule_ids:
            violations.append(
                _registry_violation(
                    "docs/adr/exceptions.yaml",
                    f"Exception references unknown rule id: {rule_id!r}",
                )
            )

    return violations
