"""Ingestion-time validation of a scenario pack as foreign input (#1578).

The uniform content-ingestion path (ADR-034) accepts a pack regardless of
provenance, but a pack is still foreign input: it must never be ingested broken,
malformed, or non-conformant. Pack *conformance* is defined by the
``aces-scenario-packs`` contract, so this module delegates to that package's
version-matched schemas and to ACES SDL parsing rather than restating the
contract. It performs the static, subprocess-free subset of the pack gate:

- ``pack.yaml`` exists, parses, and declares a pack ``name`` (catalog identity).
- ``docs/provenance-ledger.yaml`` (the pointer in ``pack.yaml``, default
  ``docs/provenance-ledger.yaml``) exists, validates against the packaged
  provenance schema, matches the pack name, and carries content-safety
  attestations that are ALL true (the exclusion invariant that makes a pack safe
  to ingest).
- ``pack.compatibility.yaml`` (when ``pack.yaml`` references it) validates
  against the packaged compatibility schema.
- every ``sdl/*.sdl.yaml`` start-state document parses through ACES
  (``aces_sdl.parse_sdl_file``), fail-closed.

It deliberately does NOT run pack-local ``validate_*.py`` scripts, unittest
suites, git, or any subprocess: those are forbidden during ingestion (the
preflight for #1578), and that deeper gate is the pack author's CI
(``aces-pack-validate``), reflected downstream by the package-source conformance
status rather than re-run here. When ``aces-scenario-packs`` exposes a public,
subprocess-free ``validate_pack`` library API (requested upstream:
aces-scenario-packs#94), this module collapses to that single call.

Error strings are bounded and reference only field names, relative filenames, and
error *classes* — never pack bodies, SDL values, or provenance payloads, because
an ingestion error surface is quasi-public.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from aces_scenario_packs.content_ci import compatibility_schema_path, provenance_schema_path

from shared.aces.sdl_validation import validate_sdl_document
from shared.log_sanitize import safe_log_value

logger = logging.getLogger(__name__)

PACK_MANIFEST = "pack.yaml"
DEFAULT_PROVENANCE_LEDGER = "docs/provenance-ledger.yaml"
SDL_DIR = "sdl"
SDL_SUFFIX = ".sdl.yaml"

# The content-safety exclusion attestations that must ALL be true for a pack to
# be safe to ingest (mirrors the aces-scenario-packs content-safety contract:
# the policy is exclusion of real sensitive content, never a weaker class).
CONTENT_SAFETY_FLAGS = (
    "no_real_malware",
    "no_real_third_party_targets",
    "no_real_credentials",
    "no_sensitive_data",
    "offensive_tooling_boundary",
)


class PackValidationError(ValueError):
    """Raised when an incoming pack fails ingestion-time validation."""


def validate_pack(pack_root: Path) -> str:
    """Validate ``pack_root`` as foreign input; return its validated pack identity.

    The returned name is the pack's own declared identity (``pack.yaml.name``,
    confirmed to match the provenance ledger). Callers bind their catalog id to
    this validated identity so a pack cannot be registered under an arbitrary
    alias (see ``cms.services.register_pack``).

    Args:
        pack_root: Resolved, containment-checked directory of the pack.

    Returns:
        The validated pack name.

    Raises:
        PackValidationError: with a bounded, body-free summary of the violations.
    """
    errors, pack_name = _check_pack(pack_root)
    if errors:
        raise PackValidationError("; ".join(errors))
    if not pack_name:
        # Defensive: a clean validation always yields a name; guard anyway.
        raise PackValidationError("pack has no validated identity")
    return pack_name


def check_pack(pack_root: Path) -> list[str]:
    """Return a list of bounded validation errors for ``pack_root`` (``[]`` = valid)."""
    return _check_pack(pack_root)[0]


def _check_pack(pack_root: Path) -> tuple[list[str], str | None]:
    """Validate the pack; return ``(errors, validated_pack_name)``."""
    if not pack_root.is_dir():
        return ["pack root does not resolve to a directory"], None

    errors: list[str] = []
    pack_yaml = _load_yaml(pack_root / PACK_MANIFEST, PACK_MANIFEST, errors)
    pack_name: str | None = None
    if pack_yaml is None:
        errors.append(f"{PACK_MANIFEST} is missing or unreadable")
    elif not isinstance(pack_yaml, dict):
        errors.append(f"{PACK_MANIFEST} is not a mapping")
    else:
        pack_name = _check_identity(pack_yaml, errors)
        _check_provenance(pack_root, pack_yaml, pack_name, errors)
        _check_compatibility(pack_root, pack_yaml, errors)
    _check_sdl(pack_root, errors)
    return errors, pack_name


def _check_identity(pack_yaml: dict[str, Any], errors: list[str]) -> str | None:
    """Validate the pack identity block; return the declared name (or ``None``)."""
    name = pack_yaml.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{PACK_MANIFEST}: required field 'name' is missing")
        return None
    return name


def _check_provenance(pack_root: Path, pack_yaml: dict[str, Any], pack_name: str | None, errors: list[str]) -> None:
    """Validate the required provenance ledger against the packaged schema."""
    rel = pack_yaml.get("provenance_ledger", DEFAULT_PROVENANCE_LEDGER)
    ledger_path = _contained_path(pack_root, rel, "provenance_ledger", errors)
    if ledger_path is None:
        return
    if not ledger_path.is_file():
        errors.append("provenance ledger is missing")
        return
    ledger = _load_yaml(ledger_path, "provenance ledger", errors)
    if not isinstance(ledger, dict):
        errors.append("provenance ledger is not a mapping")
        return
    _validate_against_schema(ledger, provenance_schema_path(), "provenance ledger", errors)
    _check_content_safety(ledger, errors)
    ledger_name = ledger.get("pack", {}).get("name") if isinstance(ledger.get("pack"), dict) else None
    if pack_name is not None and ledger_name != pack_name:
        errors.append("provenance ledger: pack name does not match pack.yaml")


def _check_content_safety(ledger: dict[str, Any], errors: list[str]) -> None:
    """Every content-safety attestation must be present and true (exclusion policy)."""
    safety = ledger.get("content_safety")
    if not isinstance(safety, dict):
        errors.append("provenance ledger: content_safety block is missing")
        return
    for flag in CONTENT_SAFETY_FLAGS:
        if safety.get(flag) is not True:
            errors.append(f"provenance ledger: content_safety.{flag} must be true")


def _check_compatibility(pack_root: Path, pack_yaml: dict[str, Any], errors: list[str]) -> None:
    """Validate the compatibility manifest against the packaged schema, when referenced."""
    rel = pack_yaml.get("compatibility_manifest")
    if rel is None:
        return
    manifest_path = _contained_path(pack_root, rel, "compatibility_manifest", errors)
    if manifest_path is None:
        return
    if not manifest_path.is_file():
        errors.append("compatibility manifest is referenced but missing")
        return
    manifest = _load_yaml(manifest_path, "compatibility manifest", errors)
    if not isinstance(manifest, dict):
        errors.append("compatibility manifest is not a mapping")
        return
    _validate_against_schema(manifest, compatibility_schema_path(), "compatibility manifest", errors)


def _check_sdl(pack_root: Path, errors: list[str]) -> None:
    """Every ``sdl/*.sdl.yaml`` start state must parse through ACES (fail-closed)."""
    sdl_dir = pack_root / SDL_DIR
    docs = sorted(sdl_dir.glob(f"*{SDL_SUFFIX}")) if sdl_dir.is_dir() else []
    if not docs:
        errors.append(f"{SDL_DIR}/ has no *{SDL_SUFFIX} start-state document")
        return
    for doc in docs:
        # aces-sdl is confined to shared.aces (ADR-031-R1); go through the seam.
        # Only the error CLASS is surfaced, never raw ACES text (SDL fragments).
        error_class = validate_sdl_document(doc)
        if error_class is not None:
            errors.append(f"{SDL_DIR}/{doc.name}: does not parse as ACES SDL ({error_class})")


def _contained_path(pack_root: Path, rel: object, field: str, errors: list[str]) -> Path | None:
    """Resolve a pack-relative reference, rejecting traversal outside the pack root."""
    if not isinstance(rel, str) or not rel.strip():
        errors.append(f"{field}: reference must be a non-empty relative path")
        return None
    root = pack_root.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        errors.append(f"{field}: reference escapes the pack root")
        return None
    return candidate


def _validate_against_schema(doc: object, schema_path: str, label: str, errors: list[str]) -> None:
    """Validate ``doc`` against a packaged JSON schema, emitting body-free errors."""
    try:
        schema = yaml.safe_load(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        errors.append(f"{label}: packaged schema is unreadable")
        return
    validator = jsonschema.Draft202012Validator(schema)
    for err in validator.iter_errors(doc):
        # Emit only the JSON path and the failing keyword — never err.message,
        # which echoes the offending value.
        location = "/".join(str(part) for part in err.absolute_path) or "$"
        errors.append(f"{label}: schema violation '{err.validator}' at {location}")


def _load_yaml(path: Path, label: str, errors: list[str]) -> Any:
    """Safe-load a YAML document, recording a bounded error on failure."""
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError):
        logger.warning("pack validation: unreadable %s at %s", label, safe_log_value(str(path.name)))
        errors.append(f"{label} is unreadable or not valid YAML")
        return None
