"""ADR registry integrity and interface-contract checks."""
from __future__ import annotations

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
_ADR_INDEX_PATH = "docs/adr/index.yaml"
_ADR_EXCEPTIONS_PATH = "docs/adr/exceptions.yaml"


def load_adr_registry(repo_root: Path) -> list[dict[str, object]]:
    """Load and validate the ADR registry shape."""
    path = repo_root / "docs" / "adr" / "index.yaml"
    data = _load_json_yaml(path)
    if not isinstance(data, list):
        raise ValueError(f"{_ADR_INDEX_PATH} must contain a top-level list")
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
    actual = set(value)
    if len(value) != len(actual):
        message = f"{field} must not contain duplicates"
    elif actual != expected:
        message = f"{field} must contain exactly {sorted(expected)}; got {sorted(actual)}"
    else:
        return []
    return [message]


def _interface_contract_kind_error(contract: dict[str, object], adr_id: str) -> str | None:
    """Return the contract kind error, or None when the declared kind is supported."""
    expected_kind = REQUIRED_INTERFACE_CONTRACTS.get(adr_id)
    kind = contract.get("kind")
    if expected_kind is not None and kind != expected_kind:
        return f"{adr_id} interface_contract kind must be {expected_kind!r}"
    if kind != "range-substrate/v1":
        return f"{adr_id} interface_contract has unsupported kind {kind!r}"
    return None


def _validate_contract_conformance(contract: dict[str, object], adr_id: str) -> list[str]:
    """Validate the conformance obligations declared by an interface contract."""
    conformance = contract.get("conformance")
    if not isinstance(conformance, dict):
        return [f"{adr_id} interface_contract.conformance must be an object"]
    return [
        f"{adr_id} interface_contract.conformance.{obligation} must be true"
        for obligation in ("shared_black_box_suite", "real_provider_promotion_evidence")
        if conformance.get(obligation) is not True
    ]


def _validate_contract_adapters(contract: dict[str, object], adr_id: str) -> list[str]:
    """Validate the initial and deferred adapter sets declared by a contract."""
    adapters = contract.get("adapters")
    if not isinstance(adapters, dict):
        return [f"{adr_id} interface_contract.adapters must be an object"]

    errors = _validate_exact_string_members(
        adapters.get("initial"),
        RANGE_SUBSTRATE_INITIAL_ADAPTERS,
        f"{adr_id} interface_contract.adapters.initial",
    )
    errors.extend(
        _validate_exact_string_members(
            adapters.get("deferred"),
            RANGE_SUBSTRATE_DEFERRED_ADAPTERS,
            f"{adr_id} interface_contract.adapters.deferred",
        )
    )
    return errors


def _is_declared_operation_list(operations: object) -> bool:
    """True for a non-empty, duplicate-free list of declared substrate operations."""
    if not isinstance(operations, list) or not operations:
        return False
    if not all(isinstance(operation, str) for operation in operations):
        return False
    return set(operations).issubset(RANGE_SUBSTRATE_OPERATIONS) and len(operations) == len(set(operations))


def _issue_reference_mapping_is_valid(mapping: dict[str, object]) -> bool:
    """True when a mapping is out-of-scope or maps exclusively to declared operations."""
    mapping_fields = set(mapping)
    if mapping_fields == {"disposition"} and mapping["disposition"] == "out-of-scope":
        return True
    return mapping_fields == {"operations"} and _is_declared_operation_list(mapping.get("operations"))


def _issue_reference_error(mapping: object, reference: object, adr_id: str) -> str | None:
    """Return the error for one issue-reference mapping, or None when it is valid."""
    if not isinstance(mapping, dict):
        return f"{adr_id} issue reference {reference} mapping must be an object"
    if _issue_reference_mapping_is_valid(mapping):
        return None
    return (
        f"{adr_id} issue reference {reference} must exclusively map to a "
        "non-empty, duplicate-free list of declared operations or have only "
        "disposition 'out-of-scope'"
    )


def _validate_contract_issue_references(contract: dict[str, object], adr_id: str) -> list[str]:
    """Validate the issue-reference map declared by an interface contract."""
    references = contract.get("issue_references")
    if not isinstance(references, dict):
        return [f"{adr_id} interface_contract.issue_references must be an object"]

    errors: list[str] = []
    actual_references = set(references)
    if actual_references != RANGE_SUBSTRATE_ISSUE_REFERENCES:
        errors.append(
            f"{adr_id} interface_contract.issue_references must contain exactly "
            f"{sorted(RANGE_SUBSTRATE_ISSUE_REFERENCES)}; got {sorted(actual_references)}"
        )
    for reference, mapping in references.items():
        error = _issue_reference_error(mapping, reference, adr_id)
        if error is not None:
            errors.append(error)
    return errors


def validate_interface_contract(contract: object, adr_id: str) -> list[str]:
    """Validate executable invariants declared by a typed ADR interface contract."""
    if not isinstance(contract, dict):
        return [f"{adr_id} interface_contract must be an object"]

    kind_error = _interface_contract_kind_error(contract, adr_id)
    if kind_error is not None:
        return [kind_error]

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
    errors.extend(_validate_contract_conformance(contract, adr_id))
    errors.extend(_validate_contract_adapters(contract, adr_id))
    errors.extend(_validate_contract_issue_references(contract, adr_id))
    return errors


def _check_adr_entry(
    entry: dict[str, object],
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
                _ADR_INDEX_PATH,
                f"ADR entry {entry.get('id', '<missing-id>')} is missing keys: {sorted(missing)}",
            )
        )
        return

    adr_id = entry["id"]
    if adr_id in adr_ids:
        violations.append(_registry_violation(_ADR_INDEX_PATH, f"Duplicate ADR id: {adr_id}"))
    adr_ids.add(adr_id)

    interface_contract = entry.get("interface_contract")
    if adr_id in REQUIRED_INTERFACE_CONTRACTS and interface_contract is None:
        violations.append(
            _registry_violation(
                _ADR_INDEX_PATH,
                f"{adr_id} must define interface_contract kind "
                f"{REQUIRED_INTERFACE_CONTRACTS[adr_id]!r}",
            )
        )
    elif interface_contract is not None:
        for error in validate_interface_contract(interface_contract, adr_id):
            violations.append(_registry_violation(_ADR_INDEX_PATH, error))

    rules = entry.get("rules", [])
    if not isinstance(rules, list):
        violations.append(
            _registry_violation(_ADR_INDEX_PATH, f"{adr_id} rules must be a list")
        )
        return

    for rule in rules:
        rule_id = rule.get("id")
        if not rule_id:
            violations.append(
                _registry_violation(
                    _ADR_INDEX_PATH, f"{adr_id} has a rule without an id"
                )
            )
            continue
        if rule_id in rule_ids:
            violations.append(
                _registry_violation(_ADR_INDEX_PATH, f"Duplicate rule id: {rule_id}")
            )
        rule_ids.add(rule_id)


def check_adr_registry(repo_root: Path, files: list[str] | None) -> list[Violation]:
    """Validate the ADR registry and exception references."""
    # the registry is validated whole on every run, not scoped to the change set
    del files
    violations: list[Violation] = []

    try:
        registry = load_adr_registry(repo_root)
        exceptions = load_adr_exceptions(repo_root)
    except (OSError, ValueError) as err:
        return [Violation("adr-registry", "ADR-REGISTRY", "docs/adr", str(err))]

    for error in validate_adr_exceptions(exceptions):
        violations.append(_registry_violation(_ADR_EXCEPTIONS_PATH, error))

    adr_ids: set[str] = set()
    rule_ids: set[str] = set()
    for entry in registry:
        _check_adr_entry(entry, adr_ids, rule_ids, violations)

    for exception in exceptions:
        rule_id = exception.get("rule_id")
        if not rule_id or rule_id not in rule_ids:
            violations.append(
                _registry_violation(
                    _ADR_EXCEPTIONS_PATH,
                    f"Exception references unknown rule id: {rule_id!r}",
                )
            )

    return violations
