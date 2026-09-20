"""Reusable GCE base-image consumption for the supported GCP tenant bootstrap.

A fresh GCP tenant should not re-bake its guest images. At bootstrap time this
module discovers the minimum base guest set (Kali, Ubuntu, DC) from the public
GHCR packages published by ``packer-gcp.yml``, validates each artifact and its
provenance, pins an immutable digest, and imports the disk as a *native GCE
image* in the target project - reusing an existing image when the digest is
unchanged, creating a traceable new one when it changes. The caller wires the
resulting ``GCP_RANGE_*_IMAGE`` references into the deployment (#2309).

Sourcing is hard-coded to public GHCR. Private packages (#2312) and a shipped
default image-pack installed through the content-pack/adapter seam are separate
follow-ups.

Subprocess calls go through ``bootstrap_core.run_cmd`` / ``gcloud_resource_exists``
so tests mock that boundary (ADR-019). The parsing/validation helpers are pure.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bootstrap_core import (
    header,
    info,
    run_cmd,
    subheader,
    success,
)

__all__ = [
    "BASE_IMAGE_ROLES",
    "BaseImageError",
    "ImportedImage",
    "ResolvedArtifact",
    "discover_and_import",
    "import_base_image",
    "package_for_role",
    "render_range_image_env",
    "resolve_artifact",
]

# The minimum reusable base set the supported GCE tenant bootstrap needs.
BASE_IMAGE_ROLES = ("kali", "ubuntu", "dc")

# OCI contract published by packer-gcp.yml (publish_target=ghcr).
GCE_IMAGE_ARTIFACT_TYPE = "application/vnd.shifter.gce-image.v1"
GCE_IMAGE_MEDIA_TYPE = "application/vnd.shifter.gce-image.tar.gz"

# GHCR owner the base-image packages are published under.
GHCR_OWNER = "brad-edwards"

# Each base role maps to the GCE range-cell image env var the runtime consumes.
# ubuntu is the default unkeyed Linux/host profile (GCP_RANGE_LINUX_IMAGE).
_ROLE_TO_RANGE_ENV = {
    "kali": "GCP_RANGE_KALI_IMAGE",
    "ubuntu": "GCP_RANGE_LINUX_IMAGE",
    "dc": "GCP_RANGE_DC_IMAGE",
}

# Windows-family roles need the WINDOWS guest OS feature on re-import. The
# premium Windows license is not carried by a raw disk export/import, so the DC
# image imported from GHCR is verified for boot/licensing in the #2309 proof-2
# tenant run.
_WINDOWS_ROLES = frozenset({"dc"})

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DISCOVERY_TAG_RE = re.compile(r"^gce-(\d+)$")


class BaseImageError(RuntimeError):
    """A base-image artifact is missing, incompatible, or failed to import."""


@dataclass(frozen=True)
class ResolvedArtifact:
    """A validated, digest-pinned base-image artifact ready to import."""

    role: str
    package: str  # e.g. ghcr.io/brad-edwards/shifter-gce-kali
    digest: str  # sha256:<64 hex>
    revision: str  # provenance: the protected build source revision

    @property
    def pinned_reference(self) -> str:
        """Immutable ``oci://<package>@<digest>`` reference (never a tag)."""
        return f"oci://{self.package}@{self.digest}"

    @property
    def short_digest(self) -> str:
        """First 12 hex of the digest, for deterministic image naming/reuse."""
        return self.digest.split(":", 1)[1][:12]


@dataclass(frozen=True)
class ImportedImage:
    """A native GCE image made available for one base role."""

    role: str
    image_name: str
    image_ref: str  # projects/<project>/global/images/<name> (a GCP_RANGE_*_IMAGE value)
    digest: str
    reused: bool


def package_for_role(role: str) -> str:
    """Return the GHCR package path for a base role."""
    return f"ghcr.io/{GHCR_OWNER}/shifter-gce-{role}"


# --- Pure parsing / validation -----------------------------------------------


def _newest_discovery_tag(tags: list[str]) -> str | None:
    """Return the newest ``gce-<id>`` discovery tag, or None when there is none.

    The ``<id>`` is the source GCE image's numeric ID. GCE assigns image IDs
    monotonically increasing over time within a project, so the highest ID among
    a package's tags is the most recently built artifact - a defined ordering,
    not a guess. Every tag in a package comes from the one protected build.
    """
    numbered = []
    for tag in tags:
        match = _DISCOVERY_TAG_RE.fullmatch(tag.strip())
        if match:
            numbered.append((int(match.group(1)), tag.strip()))
    if not numbered:
        return None
    return max(numbered)[1]


def validate_artifact_manifest(manifest: dict[str, object], *, role: str) -> str:
    """Validate an OCI manifest against the GCE base-image contract.

    Returns the provenance source revision. Raises :class:`BaseImageError` when
    the artifact type, layer media type, role annotation, or provenance is wrong
    or missing, so an incompatible artifact is rejected before any GCE mutation.
    """
    if not isinstance(manifest, dict):
        raise BaseImageError("base-image manifest is not a JSON object")
    artifact_type = manifest.get("artifactType", "")
    if artifact_type != GCE_IMAGE_ARTIFACT_TYPE:
        raise BaseImageError(f"unexpected artifactType {artifact_type!r}; expected {GCE_IMAGE_ARTIFACT_TYPE}")
    layers = manifest.get("layers") or []
    disk_layers = [layer for layer in layers if layer.get("mediaType") == GCE_IMAGE_MEDIA_TYPE]
    if len(disk_layers) != 1:
        raise BaseImageError(f"expected exactly one {GCE_IMAGE_MEDIA_TYPE} layer, found {len(disk_layers)}")
    annotations = manifest.get("annotations") or {}
    declared_role = str(annotations.get("com.shifter.image.role", "")).strip()
    if declared_role != role:
        raise BaseImageError(f"artifact role annotation {declared_role!r} does not match requested role {role!r}")
    revision = str(annotations.get("org.opencontainers.image.revision", "")).strip()
    if not revision:
        raise BaseImageError("artifact is missing its build provenance (image.revision annotation)")
    return revision


def image_name_for(role: str, short_digest: str) -> str:
    """Deterministic, digest-derived GCE image name (drives idempotent reuse)."""
    return f"shifter-{role}-{short_digest}"


def render_range_image_env(imported: dict[str, ImportedImage]) -> dict[str, str]:
    """Map imported base images to their ``GCP_RANGE_*_IMAGE`` runtime values."""
    return {_ROLE_TO_RANGE_ENV[role]: image.image_ref for role, image in imported.items() if role in _ROLE_TO_RANGE_ENV}


# --- Registry / GCE boundary (mocked in tests via bootstrap_core.run_cmd) -----


def _oras_json(cmd: list[str], *, what: str) -> dict[str, object]:
    """Run an oras command that emits JSON and parse it, failing clearly."""
    result = run_cmd(cmd, check=False, capture=True)
    if result is None or result.returncode != 0:
        stderr = (getattr(result, "stderr", "") or "").strip()
        raise BaseImageError(f"{what} failed: {stderr or 'oras returned a non-zero status'}")
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BaseImageError(f"{what} returned invalid JSON: {exc}") from exc


def _discover_newest_tag(package: str) -> str:
    """List discovery tags for a package and return the newest, or fail clearly."""
    result = run_cmd(["oras", "repo", "tags", package], check=False, capture=True)
    if result is None or result.returncode != 0:
        stderr = (getattr(result, "stderr", "") or "").strip()
        raise BaseImageError(
            f"no base image found at {package}; publish it via packer-gcp.yml "
            f"(publish_target=ghcr) then re-run ({stderr or 'oras repo tags failed'})"
        )
    tags = [line for line in (result.stdout or "").splitlines() if line.strip()]
    newest = _newest_discovery_tag(tags)
    if newest is None:
        raise BaseImageError(f"package {package} has no gce-<id> base-image tags to discover")
    return newest


def _fetch_digest(package: str, tag: str) -> str:
    """Resolve a discovery tag to its immutable manifest digest."""
    descriptor = _oras_json(
        ["oras", "manifest", "fetch", "--descriptor", f"{package}:{tag}"],
        what=f"resolving digest for {package}:{tag}",
    )
    digest = str(descriptor.get("digest", "")).strip()
    if not _DIGEST_RE.fullmatch(digest):
        raise BaseImageError(f"registry did not return a valid sha256 digest for {package}:{tag}")
    return digest


def _fetch_manifest(package: str, digest: str) -> dict[str, object]:
    """Fetch the manifest for a digest-pinned reference."""
    return _oras_json(
        ["oras", "manifest", "fetch", f"{package}@{digest}"],
        what=f"fetching manifest for {package}@{digest}",
    )


def resolve_artifact(role: str) -> ResolvedArtifact:
    """Discover, digest-pin, and validate the GHCR base-image artifact for a role."""
    package = package_for_role(role)
    digest = _fetch_digest(package, _discover_newest_tag(package))
    revision = validate_artifact_manifest(_fetch_manifest(package, digest), role=role)
    return ResolvedArtifact(role=role, package=package, digest=digest, revision=revision)


def _existing_image_digest(project: str, image_name: str) -> str | None:
    """Return the OCI digest an existing GCE image records, or None if absent.

    The import writes the immutable ``oci://<package>@<digest>`` reference into
    the image description; reuse verifies the resolved digest against it (F6),
    refusing a pre-existing image whose recorded source does not match rather
    than trusting the name alone. This is a name+source binding check, not a
    cryptographic attestation - the stronger build-attestation / trusted
    image-ID-to-digest binding is tracked as a hardening follow-up.
    """
    result = run_cmd(
        ["gcloud", "compute", "images", "describe", image_name, "--project", project, "--format=value(description)"],
        check=False,
        capture=True,
    )
    if result is None or result.returncode != 0:
        return None
    description = (getattr(result, "stdout", "") or "").strip()
    match = re.search(r"@(sha256:[0-9a-f]{64})", description)
    return match.group(1) if match else ""


def _verify_image_ready(project: str, image_name: str) -> None:
    """Fail unless the freshly created GCE image reports status READY."""
    result = run_cmd(
        ["gcloud", "compute", "images", "describe", image_name, "--project", project, "--format=value(status)"],
        check=False,
        capture=True,
    )
    status = (getattr(result, "stdout", "") or "").strip()
    if status != "READY":
        raise BaseImageError(f"imported GCE image {image_name} is not READY (status={status or 'unknown'})")


def _pull_disk_tarball(artifact: ResolvedArtifact, destination: Path) -> Path:
    """Pull the digest-pinned disk tarball into ``destination`` and return it."""
    run_cmd(["oras", "pull", f"{artifact.package}@{artifact.digest}", "-o", str(destination)])
    tarballs = sorted(destination.glob("*.tar.gz"))
    if len(tarballs) != 1:
        raise BaseImageError(
            f"expected exactly one disk tarball from {artifact.pinned_reference}, found {len(tarballs)}"
        )
    return tarballs[0]


def import_base_image(
    artifact: ResolvedArtifact,
    *,
    project: str,
    staging_bucket: str,
    dry_run: bool = False,
) -> ImportedImage:
    """Import (or reuse) a validated artifact as a native GCE image.

    An unchanged digest reuses the existing digest-named image; a changed digest
    creates a new, traceably named image and moves the ``shifter-<role>`` family
    head. The staging object is removed on success and failure.
    """
    image_name = image_name_for(artifact.role, artifact.short_digest)
    family = f"shifter-{artifact.role}"
    image_ref = f"projects/{project}/global/images/{image_name}"

    recorded_digest = _existing_image_digest(project, image_name)
    if recorded_digest is not None:
        if recorded_digest != artifact.digest:
            raise BaseImageError(
                f"GCE image {image_name} already exists but records source {recorded_digest or '(none)'}, "
                f"not the resolved digest {artifact.digest}; refusing to reuse an image this import did not create"
            )
        info(f"Reusing existing GCE image {image_name} for role '{artifact.role}' (digest unchanged)")
        return ImportedImage(artifact.role, image_name, image_ref, artifact.digest, reused=True)

    if dry_run:
        info(f"[DRY-RUN] Would import {artifact.pinned_reference} as native GCE image {image_name}")
        return ImportedImage(artifact.role, image_name, image_ref, artifact.digest, reused=False)

    subheader(f"Importing '{artifact.role}' base image as native GCE image {image_name}")
    staging_uri = f"gs://{staging_bucket}/base-images/{image_name}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="shifter-gce-base-") as tmp:
        tarball = _pull_disk_tarball(artifact, Path(tmp))
        try:
            run_cmd(["gcloud", "storage", "cp", "--quiet", str(tarball), staging_uri])
            create_cmd = [
                "gcloud",
                "compute",
                "images",
                "create",
                image_name,
                "--source-uri",
                staging_uri,
                "--family",
                family,
                "--project",
                project,
                "--labels",
                f"shifter-base-role={artifact.role},shifter-base-digest={artifact.short_digest}",
                "--description",
                artifact.pinned_reference,
            ]
            if artifact.role in _WINDOWS_ROLES:
                # Mark the re-imported disk Windows/UEFI-bootable. The premium
                # Windows license is not carried by a raw export; DC boot and
                # licensing are verified in the #2309 proof-2 tenant run.
                create_cmd += ["--guest-os-features", "WINDOWS,UEFI_COMPATIBLE"]
            run_cmd(create_cmd)
            _verify_image_ready(project, image_name)
        finally:
            # Always clear the staging object so no transfer drift is left behind.
            run_cmd(["gcloud", "storage", "rm", "--quiet", staging_uri], check=False)

    success(f"Imported '{artifact.role}' as native GCE image {image_name}")
    return ImportedImage(artifact.role, image_name, image_ref, artifact.digest, reused=False)


def discover_and_import(
    *,
    project: str,
    staging_bucket: str,
    roles: tuple[str, ...] = BASE_IMAGE_ROLES,
    dry_run: bool = False,
) -> dict[str, ImportedImage]:
    """Discover, validate, and import the reusable base set as native GCE images.

    Returns the imported/reused image for each role; :func:`render_range_image_env`
    turns that into the ``GCP_RANGE_*_IMAGE`` values the runtime env consumes. A
    missing or incompatible artifact raises :class:`BaseImageError` (no silent
    Packer bake, no blind accept).
    """
    header("Reusable GCE base images (GHCR -> native GCE)")
    imported: dict[str, ImportedImage] = {}
    for role in roles:
        artifact = resolve_artifact(role)
        info(f"Resolved '{role}' -> {artifact.pinned_reference} (build {artifact.revision[:12]})")
        imported[role] = import_base_image(artifact, project=project, staging_bucket=staging_bucket, dry_run=dry_run)
    return imported
