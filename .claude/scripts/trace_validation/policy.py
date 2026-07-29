"""Validation policy for trace claims.

Pure, side-effect-free comparison of an LLM-generated claim against the AST
ground truth. This layer performs no file, process, network, or logging work;
it accepts a :class:`FunctionInfo` and a claim mapping and returns a list of
:class:`ValidationResult`. Adding a new supported claim field changes only this
dispatcher and its focused tests.
"""

from __future__ import annotations

from typing import Any

from .models import FunctionInfo, ValidationResult

# The claim fields this policy knows how to validate. A claim carrying none of
# these is not a vacuous pass: it is reported as an explicit invalid result so a
# malformed or empty claim cannot slip through as valid (fail-closed).
RECOGNIZED_CLAIM_FIELDS = frozenset(
    {"returns", "args", "annotations", "calls", "lineno", "raises"}
)


def claim_has_recognized_field(claim: dict[str, Any]) -> bool:
    """True iff the claim carries at least one field this policy validates."""
    return bool(RECOGNIZED_CLAIM_FIELDS & set(claim))


def validate_claim(
    ground_truth: FunctionInfo,
    claim: dict[str, Any],
) -> list[ValidationResult]:
    """Validate a claim against ground truth.

    Args:
        ground_truth: Extracted function info from AST
        claim: Dict with claimed properties to validate

    Returns:
        List of ValidationResults (one per field checked). A claim with no
        recognized fields yields a single explicit invalid result rather than an
        empty (vacuously passing) list.
    """
    results = []

    # Validate return type
    if "returns" in claim:
        claimed = claim["returns"]
        actual = ground_truth.returns
        # Normalize for comparison (handle None vs "None" vs missing)
        claimed_norm = str(claimed) if claimed else None
        actual_norm = str(actual) if actual else None
        valid = claimed_norm == actual_norm
        results.append(
            ValidationResult(
                valid=valid,
                field="returns",
                claimed=claimed,
                actual=actual,
                message="" if valid else "Return type mismatch",
            )
        )

    # Validate arguments
    if "args" in claim:
        claimed = set(claim["args"])
        actual = set(ground_truth.args)
        valid = claimed == actual
        results.append(
            ValidationResult(
                valid=valid,
                field="args",
                claimed=sorted(claim["args"]),
                actual=sorted(ground_truth.args),
                message="" if valid else f"Missing: {actual - claimed}, Extra: {claimed - actual}",
            )
        )

    # Validate annotations
    if "annotations" in claim:
        for arg, claimed_type in claim["annotations"].items():
            actual_type = ground_truth.annotations.get(arg)
            valid = str(claimed_type) == str(actual_type)
            results.append(
                ValidationResult(
                    valid=valid,
                    field=f"annotation:{arg}",
                    claimed=claimed_type,
                    actual=actual_type,
                    message="" if valid else f"Type annotation mismatch for {arg}",
                )
            )

    # Validate calls (check if claimed calls exist)
    if "calls" in claim:
        actual_call_names = {c["name"] for c in ground_truth.calls}
        for claimed_call in claim["calls"]:
            call_name = claimed_call if isinstance(claimed_call, str) else claimed_call.get("name")
            valid = call_name in actual_call_names
            results.append(
                ValidationResult(
                    valid=valid,
                    field="calls",
                    claimed=call_name,
                    actual=sorted(actual_call_names) if not valid else call_name,
                    message="" if valid else f"Call '{call_name}' not found in function",
                )
            )

    # Validate line number
    if "lineno" in claim:
        valid = claim["lineno"] == ground_truth.lineno
        results.append(
            ValidationResult(
                valid=valid,
                field="lineno",
                claimed=claim["lineno"],
                actual=ground_truth.lineno,
                message="" if valid else "Line number mismatch",
            )
        )

    # Validate raises
    if "raises" in claim:
        claimed_raises = set(claim["raises"])
        actual_raises = set(ground_truth.raises)
        valid = claimed_raises <= actual_raises  # Claimed should be subset of actual
        results.append(
            ValidationResult(
                valid=valid,
                field="raises",
                claimed=sorted(claimed_raises),
                actual=sorted(actual_raises),
                message="" if valid else f"Raises mismatch - not found: {claimed_raises - actual_raises}",
            )
        )

    # Fail-closed: a claim that named no recognized field validated nothing, so
    # returning an empty list would let it pass vacuously. Surface it explicitly.
    if not results:
        results.append(
            ValidationResult(
                valid=False,
                field="claim",
                claimed=sorted(claim.keys()),
                actual=sorted(RECOGNIZED_CLAIM_FIELDS),
                message="Claim carries no recognized fields to validate",
            )
        )

    return results
