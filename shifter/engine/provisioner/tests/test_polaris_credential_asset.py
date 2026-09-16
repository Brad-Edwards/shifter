"""Regression tests for the Polaris splice-helper asset path resolution."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from plans import _polaris_credential_asset as asset


def test_source_helper_path_none_at_container_depth():
    # Packaged provisioner container flattens this module to /app/plans, which has
    # only three path parents. An eager parents[3] there raised IndexError at
    # import and crashed every provisioner Job; the guard must return None.
    assert asset._source_helper_path(Path("/app/plans/_polaris_credential_asset.py")) is None


def test_source_helper_path_resolves_in_source_tree():
    resolved = Path("/x/shifter/engine/provisioner/plans/_polaris_credential_asset.py")
    assert asset._source_helper_path(resolved) == Path("/x/shifter/packer/files/polaris_splice_credential.py")


def test_helper_b64_uses_packaged_helper_when_source_absent(tmp_path, monkeypatch):
    packaged = tmp_path / "polaris-splice-credential.py"
    packaged.write_bytes(b"reviewed-helper-bytes")
    monkeypatch.setattr(asset, "_PACKAGED_HELPER", packaged)
    monkeypatch.setattr(asset, "_SOURCE_HELPER", None)  # container case
    import base64

    assert asset.splice_credential_helper_b64() == base64.b64encode(b"reviewed-helper-bytes").decode("ascii")
