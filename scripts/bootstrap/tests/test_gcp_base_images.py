"""Behavior tests for gcp_base_images: reusable GCE base-image consumption (#2309).

Covers the proof-1 surface: GHCR discovery, digest + artifact validation,
import/reuse, failure cleanup, dry-run, and the generated GCP_RANGE_*_IMAGE
references. The run_cmd boundary is mocked (ADR-019); a single fake runner
simulates the oras/gcloud side effects for integration-style tests.
"""

import json
import subprocess
from pathlib import Path

import pytest

import gcp_base_images as gbi

DIGEST = "sha256:" + "a" * 64
_ROLE_ENV = {"kali": "GCP_RANGE_KALI_IMAGE", "ubuntu": "GCP_RANGE_LINUX_IMAGE", "dc": "GCP_RANGE_DC_IMAGE"}


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


def _manifest(role="kali", revision="c" * 40):
    return {
        "artifactType": gbi.GCE_IMAGE_ARTIFACT_TYPE,
        "layers": [{"mediaType": gbi.GCE_IMAGE_MEDIA_TYPE, "digest": "sha256:" + "b" * 64}],
        "annotations": {
            "com.shifter.image.role": role,
            "org.opencontainers.image.revision": revision,
        },
    }


class FakeRunCmd:
    """Records run_cmd calls and simulates the oras/gcloud side effects.

    ``existing_description`` is the description an ``images describe`` returns for
    the reuse check: None means the image does not exist (import proceeds); a
    string is the recorded description (reuse verifies its digest).
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self.tags = "gce-2\ngce-10\ngce-3\n"
        self.digest = DIGEST
        self.status = "READY"
        self.existing_description: str | None = None
        self.fail_substr: str | None = None

    def __call__(self, cmd, dry_run=False, check=True, capture=False, profile=None):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        line = " ".join(cmd)
        if self.fail_substr and self.fail_substr in line:
            if check:
                raise SystemExit(1)
            return _completed(returncode=1, stderr="simulated failure")
        if dry_run:
            return None
        if cmd[:3] == ["oras", "repo", "tags"]:
            return _completed(stdout=self.tags)
        if cmd[:3] == ["oras", "manifest", "fetch"]:
            if "--descriptor" in cmd:
                return _completed(stdout=json.dumps({"digest": self.digest}))
            ref = next((c for c in cmd if "shifter-gce-" in c), "shifter-gce-kali")
            role = ref.split("shifter-gce-", 1)[1].split("@")[0].split(":")[0]
            return _completed(stdout=json.dumps(_manifest(role=role)))
        if cmd[:2] == ["oras", "pull"]:
            dest = Path(cmd[cmd.index("-o") + 1])
            (dest / "disk.tar.gz").write_bytes(b"rawdisk")
            return _completed()
        if cmd[:4] == ["gcloud", "compute", "images", "describe"]:
            if "--format=value(description)" in cmd:
                if self.existing_description is None:
                    return _completed(returncode=1, stderr="not found")
                return _completed(stdout=self.existing_description)
            if "--format=value(status)" in cmd:
                return _completed(stdout=self.status)
        return _completed()

    def ran(self, prefix):
        return [c for c in self.calls if c[: len(prefix)] == prefix]


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRunCmd()
    monkeypatch.setattr(gbi, "run_cmd", fake)
    return fake


class TestPureHelpers:
    def test_package_for_role_is_hard_coded_ghcr(self):
        assert gbi.package_for_role("dc") == "ghcr.io/brad-edwards/shifter-gce-dc"

    def test_newest_discovery_tag_picks_highest_numeric(self):
        assert gbi._newest_discovery_tag(["gce-2", "gce-10", "latest", "gce-3"]) == "gce-10"

    def test_newest_discovery_tag_none_when_no_match(self):
        assert gbi._newest_discovery_tag(["latest", "v1", ""]) is None

    def test_validate_manifest_returns_revision(self):
        assert gbi.validate_artifact_manifest(_manifest(role="ubuntu", revision="d" * 40), role="ubuntu") == "d" * 40

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda m: m.update({"artifactType": "application/vnd.oci.image.manifest.v1+json"}),
            lambda m: m.update({"layers": []}),
            lambda m: m.update({"layers": [{"mediaType": "application/octet-stream"}]}),
            lambda m: m["annotations"].update({"com.shifter.image.role": "windows"}),
            lambda m: m["annotations"].update({"org.opencontainers.image.revision": ""}),
        ],
    )
    def test_validate_manifest_rejects_incompatible(self, mutate):
        manifest = _manifest(role="kali")
        mutate(manifest)
        with pytest.raises(gbi.BaseImageError):
            gbi.validate_artifact_manifest(manifest, role="kali")

    def test_validate_manifest_rejects_non_object(self):
        with pytest.raises(gbi.BaseImageError):
            gbi.validate_artifact_manifest(["not", "a", "dict"], role="kali")

    def test_image_name_is_digest_derived(self):
        assert gbi.image_name_for("kali", "abc123def456") == "shifter-kali-abc123def456"

    def test_render_range_image_env_maps_roles(self):
        imported = {
            "ubuntu": gbi.ImportedImage("ubuntu", "n", "projects/p/global/images/n", DIGEST, reused=False),
            "kali": gbi.ImportedImage("kali", "k", "projects/p/global/images/k", DIGEST, reused=True),
            "dc": gbi.ImportedImage("dc", "d", "projects/p/global/images/d", DIGEST, reused=False),
        }
        assert gbi.render_range_image_env(imported) == {
            "GCP_RANGE_LINUX_IMAGE": "projects/p/global/images/n",
            "GCP_RANGE_KALI_IMAGE": "projects/p/global/images/k",
            "GCP_RANGE_DC_IMAGE": "projects/p/global/images/d",
        }


class TestResolveArtifact:
    def test_resolve_discovers_newest_and_validates(self, fake_run):
        artifact = gbi.resolve_artifact("kali")
        assert artifact.package == "ghcr.io/brad-edwards/shifter-gce-kali"
        assert artifact.digest == DIGEST
        assert artifact.revision == "c" * 40
        assert artifact.pinned_reference == f"oci://ghcr.io/brad-edwards/shifter-gce-kali@{DIGEST}"
        assert fake_run.ran(["oras", "repo", "tags"])

    def test_resolve_missing_package_raises(self, fake_run):
        fake_run.fail_substr = "repo tags"
        with pytest.raises(gbi.BaseImageError):
            gbi.resolve_artifact("kali")

    def test_resolve_rejects_role_mismatch(self, fake_run, monkeypatch):
        monkeypatch.setattr(gbi, "_fetch_manifest", lambda pkg, digest: _manifest(role="ubuntu"))
        with pytest.raises(gbi.BaseImageError):
            gbi.resolve_artifact("kali")


class TestImportBaseImage:
    def _artifact(self, role="kali"):
        return gbi.ResolvedArtifact(
            role=role,
            package=f"ghcr.io/brad-edwards/shifter-gce-{role}",
            digest=DIGEST,
            revision="c" * 40,
        )

    def test_import_creates_native_image_and_cleans_staging(self, fake_run):
        imported = gbi.import_base_image(self._artifact("kali"), project="proj", staging_bucket="stage")
        assert imported.reused is False
        assert imported.image_name == "shifter-kali-" + "a" * 12
        assert imported.image_ref == "projects/proj/global/images/shifter-kali-" + "a" * 12
        create = next(c for c in fake_run.calls if c[:4] == ["gcloud", "compute", "images", "create"])
        assert "--source-uri" in create
        assert "gs://stage/base-images/shifter-kali-aaaaaaaaaaaa.tar.gz" in create
        assert "shifter-base-role=kali,shifter-base-digest=" + "a" * 12 in create
        assert "--family" in create and "shifter-kali" in create
        # The full OCI reference is recorded so reuse can verify it (F6).
        assert f"oci://ghcr.io/brad-edwards/shifter-gce-kali@{DIGEST}" in create
        # Staging object is removed after a successful import (no drift).
        assert fake_run.ran(["gcloud", "storage", "rm"])

    def test_import_reuses_matching_existing_image(self, fake_run):
        fake_run.existing_description = f"oci://ghcr.io/brad-edwards/shifter-gce-ubuntu@{DIGEST}"
        imported = gbi.import_base_image(self._artifact("ubuntu"), project="proj", staging_bucket="stage")
        assert imported.reused is True
        assert not fake_run.ran(["gcloud", "compute", "images", "create"])
        assert not fake_run.ran(["oras", "pull"])

    def test_import_refuses_existing_image_with_mismatched_digest(self, fake_run):
        # F6: a pre-existing image whose recorded source is not the resolved
        # digest is refused rather than blindly reused.
        fake_run.existing_description = "oci://ghcr.io/brad-edwards/shifter-gce-kali@sha256:" + "e" * 64
        with pytest.raises(gbi.BaseImageError):
            gbi.import_base_image(self._artifact("kali"), project="proj", staging_bucket="stage")
        assert not fake_run.ran(["gcloud", "compute", "images", "create"])

    def test_import_dc_marks_windows_guest_os_feature(self, fake_run):
        gbi.import_base_image(self._artifact("dc"), project="proj", staging_bucket="stage")
        create = next(c for c in fake_run.calls if c[:4] == ["gcloud", "compute", "images", "create"])
        assert "--guest-os-features" in create
        assert "WINDOWS,UEFI_COMPATIBLE" in create

    def test_import_cleans_staging_even_when_create_fails(self, fake_run):
        fake_run.fail_substr = "images create"
        with pytest.raises(SystemExit):
            gbi.import_base_image(self._artifact("kali"), project="proj", staging_bucket="stage")
        assert fake_run.ran(["gcloud", "storage", "rm"])

    def test_import_fails_when_image_not_ready(self, fake_run):
        fake_run.status = "PENDING"
        with pytest.raises(gbi.BaseImageError):
            gbi.import_base_image(self._artifact("kali"), project="proj", staging_bucket="stage")

    def test_dry_run_transfers_nothing(self, fake_run):
        # F4: --dry-run must not pull payloads or mutate the cloud.
        imported = gbi.import_base_image(self._artifact("kali"), project="proj", staging_bucket="stage", dry_run=True)
        assert imported.reused is False
        assert not fake_run.ran(["oras", "pull"])
        assert not fake_run.ran(["gcloud", "compute", "images", "create"])
        assert not fake_run.ran(["gcloud", "storage", "cp"])


class TestDiscoverAndImport:
    def test_imports_all_roles_and_renders_env(self, fake_run):
        imported = gbi.discover_and_import(project="proj", staging_bucket="stage")
        assert set(imported) == set(gbi.BASE_IMAGE_ROLES)
        env = gbi.render_range_image_env(imported)
        assert set(env) == {"GCP_RANGE_KALI_IMAGE", "GCP_RANGE_LINUX_IMAGE", "GCP_RANGE_DC_IMAGE"}
        for role in gbi.BASE_IMAGE_ROLES:
            assert env[_ROLE_ENV[role]] == f"projects/proj/global/images/shifter-{role}-" + "a" * 12

    def test_missing_artifact_fails_loud(self, fake_run):
        fake_run.fail_substr = "repo tags"
        with pytest.raises(gbi.BaseImageError):
            gbi.discover_and_import(project="proj", staging_bucket="stage")

    def test_dry_run_discovers_without_transfer(self, fake_run):
        gbi.discover_and_import(project="proj", staging_bucket="stage", dry_run=True)
        assert not fake_run.ran(["oras", "pull"])
        assert not fake_run.ran(["gcloud", "compute", "images", "create"])
