"""Inspect a read-only mounted candidate using independently installed expectations."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def contained_path(root: Path, relative: str, *, leaf_link: bool = False) -> Path:
    """Reject links through any candidate-controlled component of an expected file."""
    current = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink() and not (leaf_link and index == len(parts) - 1):
            raise ValueError("candidate contains unexpected filesystem indirection")
    return current


def regular_file(root: Path, relative: str) -> Path:
    """Read only bounded regular files below an unredirected candidate path."""
    current = contained_path(root, relative)
    if not current.is_file() or current.stat().st_size > 65536:
        raise ValueError("candidate file is missing or exceeds the verification bound")
    return current


def verify_root(root: Path, context: Path) -> dict[str, str]:
    """Measure concrete service files and the supported OS; never execute the disk."""
    root = root.resolve(strict=True)
    for source, target in (
        ("server.py", "opt/shifter-http-smoke/server.py"),
        ("shifter-http-smoke.service", "etc/systemd/system/shifter-http-smoke.service"),
    ):
        actual = regular_file(root, target).read_bytes()
        if hashlib.sha256(actual).digest() != hashlib.sha256((context / source).read_bytes()).digest():
            raise ValueError("candidate does not contain the expected service bytes")
    enabled = contained_path(
        root, "etc/systemd/system/multi-user.target.wants/shifter-http-smoke.service", leaf_link=True
    )
    if not enabled.is_symlink() or str(enabled.readlink()) != "../shifter-http-smoke.service":
        raise ValueError("candidate service is not enabled")
    os_release = root / "etc/os-release"
    resolved = os_release.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved.stat().st_size > 65536:
        raise ValueError("candidate operating system identity is unavailable")
    values = dict(  # NOSONAR -- Ruff C416 requires the direct constructor here.
        line.split("=", 1) for line in resolved.read_text().splitlines() if "=" in line
    )
    identity = (values.get("ID", "").strip('"'), values.get("VERSION_ID", "").strip('"'))
    if identity != ("ubuntu", "22.04"):
        raise ValueError("candidate operating system is outside the specimen profile")
    if list((root / "etc/ssh").glob("ssh_host_*")) or (root / "var/lib/shifter-preparation").exists():
        raise ValueError("candidate retains build material or host identity")
    return {"operating-system": "ubuntu:22.04"}


if __name__ == "__main__":
    print(json.dumps(verify_root(Path(sys.argv[1]), Path(__file__).parent), sort_keys=True))  # noqa: T201
