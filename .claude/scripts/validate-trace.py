#!/usr/bin/env python3
"""AST-based validation for code trace analysis.

Extracts ground truth from Python source files to validate LLM-generated
code analysis. Compares claimed signatures, calls, and structure against
actual AST.

Usage:
    # Extract function info as JSON
    python validate-trace.py extract <file> <function>

    # Validate a claim against source
    python validate-trace.py validate <file> <function> '<json_claim>'

    # Batch validate from a trace file
    python validate-trace.py batch <trace_file>

Examples:
    python validate-trace.py extract cms/services.py create_range
    python validate-trace.py validate cms/services.py create_range '{"returns": "RangeContext"}'

The implementation lives in the ``trace_validation`` package alongside this
script (AST extraction, validation policy, reporting, and this CLI boundary).
This file remains the stable command-line entrypoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python .claude/scripts/validate-trace.py ...`` to import the sibling
# package regardless of the current working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from trace_validation.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
