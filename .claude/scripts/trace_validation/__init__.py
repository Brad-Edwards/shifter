"""AST-based trace validation, split into focused layers.

Public surface:

- ``models``    — the ``FunctionInfo`` / ``ValidationResult`` /
  ``TraceValidationReport`` contract dataclasses (defined once).
- ``extractor`` — AST fact extraction (``extract_function_info``).
- ``policy``    — pure claim comparison (``validate_claim``).
- ``report``    — block parsing + aggregation (``parse_validation_block``,
  ``validate_trace_file``).
- ``cli``       — argv / filesystem / stdout / exit boundary (``main``).
"""

from __future__ import annotations

from .extractor import (
    extract_calls,
    extract_function_info,
    extract_raises,
    get_annotation_str,
)
from .models import FunctionInfo, TraceValidationReport, ValidationResult
from .policy import RECOGNIZED_CLAIM_FIELDS, claim_has_recognized_field, validate_claim
from .report import parse_validation_block, report_has_failures, validate_trace_file

__all__ = [
    "FunctionInfo",
    "ValidationResult",
    "TraceValidationReport",
    "get_annotation_str",
    "extract_calls",
    "extract_raises",
    "extract_function_info",
    "validate_claim",
    "claim_has_recognized_field",
    "RECOGNIZED_CLAIM_FIELDS",
    "parse_validation_block",
    "validate_trace_file",
    "report_has_failures",
]
