"""Contract types for trace validation.

These three dataclasses are the single serialization contract shared across the
extractor, policy, and reporting layers. They are defined once here; no layer
reproduces them as parallel dictionaries, DTOs, or exception types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FunctionInfo:
    """Ground truth extracted from AST."""

    name: str
    file: str
    lineno: int
    end_lineno: int | None
    args: list[str]
    annotations: dict[str, str]
    returns: str | None
    defaults: dict[str, str]
    decorators: list[str]
    calls: list[dict[str, Any]]
    raises: list[str]
    docstring: str | None
    is_async: bool = False
    is_method: bool = False
    class_name: str | None = None


@dataclass
class ValidationResult:
    """Result of validating a claim against ground truth."""

    valid: bool
    field: str
    claimed: Any
    actual: Any
    message: str


@dataclass
class TraceValidationReport:
    """Full validation report for a trace file."""

    total_functions: int = 0
    validated: int = 0
    passed: int = 0
    failed: int = 0
    not_found: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
