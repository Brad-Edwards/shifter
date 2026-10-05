#!/bin/bash
# TEMPORARY, GCE images only: bakes the Claude Code binary until GCP acquires and
# delivers it like AWS does (#2479). AWS images never contain it (#2463). Delete
# this script and its template references with #2479.
set -euo pipefail

echo "=== Installing Claude Code (GCE, until #2479) ==="
npm install -g --ignore-scripts @anthropic-ai/claude-code
