"""Load the reviewed Polaris splice helper bundled with the provisioner."""

from __future__ import annotations

import base64
from pathlib import Path


def _source_helper_path(resolved: Path) -> Path | None:
    """Source-tree fallback path for the helper, or None when unavailable.

    In the dev/test source tree this module lives at
    shifter/engine/provisioner/plans/, so ``parents[3]`` is the repo's shifter/
    dir and the helper is shifter/packer/files/polaris_splice_credential.py. The
    packaged provisioner container flattens this module to /app/plans, which has
    only three path parents; an eager ``parents[3]`` there raises IndexError at
    import time and crashes every provisioner Job. Guard the depth and return
    None in the container, where ``_PACKAGED_HELPER`` (/app/assets) is used.
    """
    parents = resolved.parents
    if len(parents) <= 3:
        return None
    return parents[3] / "packer" / "files" / "polaris_splice_credential.py"


_HELPER_RESOLVED = Path(__file__).resolve()
_PACKAGED_HELPER = _HELPER_RESOLVED.parents[1] / "assets" / "polaris-splice-credential.py"
_SOURCE_HELPER = _source_helper_path(_HELPER_RESOLVED)


def splice_credential_helper_b64() -> str:
    """Return the exact helper bytes for secret-free host staging."""
    for path in (_PACKAGED_HELPER, _SOURCE_HELPER):
        if path is not None and path.is_file():
            return base64.b64encode(path.read_bytes()).decode("ascii")
    raise RuntimeError("Polaris splice credential helper is not packaged")
