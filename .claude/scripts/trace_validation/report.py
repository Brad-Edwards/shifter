"""Trace-file parsing, aggregation, and serialization.

This layer owns extracting ``VALIDATION_BLOCK`` payloads from trace markdown and
aggregating per-function validation into a :class:`TraceValidationReport`.
``parse_validation_block`` is pure (content in, structured blocks out, no
filesystem work). ``validate_trace_file`` reads the trace file it is handed a
path to and orchestrates extraction + policy, but performs no argv or exit-code
work — that stays at the CLI boundary.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .extractor import extract_function_info
from .models import TraceValidationReport
from .policy import validate_claim

# Key used to mark a block that could not be parsed as JSON. A malformed block
# is not dropped silently (which would let it pass vacuously); it is surfaced as
# an explicit invalid result.
_PARSE_ERROR_KEY = "__parse_error__"


def parse_validation_block(content: str) -> list[dict[str, Any]]:
    """Extract VALIDATION_BLOCK JSON payloads from markdown content.

    Returns one entry per recognized block. A block whose JSON does not parse is
    represented as ``{"__parse_error__": "<message>"}`` rather than being dropped,
    so malformed blocks remain visible to the report.
    """
    # Recognize a block by its delimiters ALONE, then validate the payload. A
    # payload that is not a complete JSON object (truncated, missing braces, or a
    # JSON array) must be reported as INVALID rather than failing the regex and
    # vanishing from the report (which would let a batch run exit successfully).
    pattern = r'<!-- VALIDATION_BLOCK\s*(.*?)\s*END_VALIDATION_BLOCK -->'
    matches = re.findall(pattern, content, re.DOTALL)
    blocks: list[dict[str, Any]] = []
    for raw in matches:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            blocks.append({_PARSE_ERROR_KEY: f"malformed VALIDATION_BLOCK JSON: {exc}"})
            continue
        if not isinstance(parsed, dict):
            blocks.append(
                {_PARSE_ERROR_KEY: f"VALIDATION_BLOCK payload is not a JSON object ({type(parsed).__name__})"}
            )
            continue
        blocks.append(parsed)
    return blocks


def report_has_failures(report: TraceValidationReport) -> bool:
    """True iff any block failed, was invalid, or could not be resolved.

    The CLI uses this to choose a non-zero exit for ``batch``.
    """
    return report.failed > 0 or report.not_found > 0


def validate_trace_file(trace_path: str) -> TraceValidationReport:
    """Validate all functions documented in a trace file.

    Args:
        trace_path: Path to markdown trace file with VALIDATION_BLOCKs

    Returns:
        TraceValidationReport with all results
    """
    path = Path(trace_path)
    if not path.exists():
        return TraceValidationReport()

    content = path.read_text()
    blocks = parse_validation_block(content)

    report = TraceValidationReport(total_functions=len(blocks))

    for block in blocks:
        # Malformed block JSON: explicit failure, never a silent drop.
        if _PARSE_ERROR_KEY in block:
            report.failed += 1
            report.results.append({
                "function": "<unparseable>",
                "status": "INVALID",
                "message": block[_PARSE_ERROR_KEY],
            })
            continue

        file_path = block.get("file")
        func_name = block.get("function")
        class_name = block.get("class")

        # Missing required selectors: explicit failure, never a silent drop.
        if not file_path or not func_name:
            report.failed += 1
            report.results.append({
                "function": f"{file_path or '?'}:{func_name or '?'}",
                "status": "INVALID",
                "message": "Validation block is missing a 'file' or 'function' selector",
            })
            continue

        report.validated += 1

        ground_truth = extract_function_info(file_path, func_name, class_name)
        if ground_truth is None:
            report.not_found += 1
            report.results.append({
                "function": f"{file_path}:{func_name}",
                "status": "NOT_FOUND",
                "message": "Could not extract function from source",
            })
            continue

        results = validate_claim(ground_truth, block)

        all_passed = all(r.valid for r in results)
        if all_passed:
            report.passed += 1
        else:
            report.failed += 1

        report.results.append({
            "function": f"{file_path}:{func_name}",
            "status": "PASS" if all_passed else "FAIL",
            "validations": [asdict(r) for r in results],
        })

    return report
