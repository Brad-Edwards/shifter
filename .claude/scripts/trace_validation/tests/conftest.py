"""Make the ``trace_validation`` package importable during tests.

The package lives at ``.claude/scripts/trace_validation``; its import root is
``.claude/scripts``. Adding that directory to ``sys.path`` lets the tests
``import trace_validation`` regardless of the pytest invocation directory,
without importing the ``validate-trace.py`` shim (which would run ``main``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]  # .claude/scripts
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
