"""Command-line boundary for trace validation.

This is the only layer that owns argv parsing, filesystem access decisions,
stdout/stderr, and process exit status. ``main`` returns an integer exit code
and never calls ``sys.exit`` itself, so tests can drive the CLI boundary without
importing a module that terminates the process. Machine-readable JSON is written
to stdout; human diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from .extractor import extract_function_info
from .policy import claim_has_recognized_field, validate_claim
from .report import report_has_failures, validate_trace_file

_USAGE = """AST-based validation for code trace analysis.

Usage:
    # Extract function info as JSON
    python validate-trace.py extract <file> <function> [class]

    # Validate a claim against source
    python validate-trace.py validate <file> <function> '<json_claim>'

    # Batch validate from a trace file
    python validate-trace.py batch <trace_file>

Examples:
    python validate-trace.py extract cms/services.py create_range
    python validate-trace.py validate cms/services.py create_range '{"returns": "RangeContext"}'
"""


def _err(message: str) -> None:
    """Write a human diagnostic to stderr (never stdout, which carries JSON)."""
    print(message, file=sys.stderr)


def _cmd_extract(args: list[str]) -> int:
    if len(args) < 2:
        _err("Usage: validate-trace.py extract <file> <function> [class]")
        return 1

    file_path = args[0]
    func_name = args[1]
    class_name = args[2] if len(args) > 2 else None

    info = extract_function_info(file_path, func_name, class_name)
    if info is None:
        print(json.dumps({"error": "Function not found"}))
        return 1

    print(json.dumps(asdict(info), indent=2))
    return 0


def _cmd_validate(args: list[str]) -> int:
    if len(args) < 3:
        _err("Usage: validate-trace.py validate <file> <function> '<json_claim>'")
        return 1

    file_path = args[0]
    func_name = args[1]
    claim_json = args[2]

    try:
        claim = json.loads(claim_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        return 1

    info = extract_function_info(file_path, func_name)
    if info is None:
        print(json.dumps({"error": "Function not found"}))
        return 1

    results = validate_claim(info, claim)
    output = {
        "valid": all(r.valid for r in results),
        "results": [asdict(r) for r in results],
    }
    print(json.dumps(output, indent=2))
    # A legitimate claim that simply mismatches keeps the historical exit 0 (the
    # tool worked and reported the discrepancy). A claim carrying no recognized
    # field is a malformed input, so it fails closed with a non-zero exit.
    return 0 if claim_has_recognized_field(claim) else 1


def _cmd_batch(args: list[str]) -> int:
    if len(args) < 1:
        _err("Usage: validate-trace.py batch <trace_file>")
        return 1

    trace_path = args[0]
    if not Path(trace_path).exists():
        print(json.dumps({"error": f"Trace file not found: {trace_path}"}))
        return 1

    report = validate_trace_file(trace_path)
    print(json.dumps(asdict(report), indent=2))
    # Non-zero when any block failed, was invalid, or could not be resolved.
    return 1 if report_has_failures(report) else 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch a trace-validation command. Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_USAGE)
        return 1

    command, rest = args[0], args[1:]

    if command == "extract":
        return _cmd_extract(rest)
    if command == "validate":
        return _cmd_validate(rest)
    if command == "batch":
        return _cmd_batch(rest)

    _err(f"Unknown command: {command}")
    print(_USAGE)
    return 1
