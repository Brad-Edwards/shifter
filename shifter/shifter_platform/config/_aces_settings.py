"""ACES operation sidecar retention/cleanup Django settings (#1277).

Extracted into a ``config/_*.py`` module like ``config/_guacamole_settings.py``
so ``config/settings.py`` stays under the 500-line cap (Sonar S104). Importing
this module has no side effects beyond binding the module-level constants used
in the re-export.

These are the operator-visible, non-secret knobs for ACES runtime-snapshot (and
adjacent operation-record) retention. ``AcesOperationRecord`` rows are bounded
operational observations, not an archive: each row is written with an indexed
``retention_expires_at`` derived from ``ACES_OPERATION_RECORD_RETENTION_DAYS``,
and the dedicated pruning service (``run_aces_operation_record_prune``) deletes
rows past that boundary in bounded batches. Setting the retention days to ``0``
(or negative) disables the expiry stamp, so no row is ever pruned -- an
opt-out-safe default. The interval/batch knobs mirror the guacamole bootstrap
prune service (``config/_guacamole_settings.py``); the reads use literal
``os.environ.get`` so the generated ``config/env-manifest.json`` picks them up.
The design contract lives in
``docs/architecture/aces-snapshot-retention-redaction-audit-preflight-1277.md``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "ACES_CATALOG_CUTOVERS",
    "ACES_NATIVE_PROVISIONING_ENABLED",
    "ACES_OPERATION_RECORD_PRUNE_BATCH_SIZE",
    "ACES_OPERATION_RECORD_PRUNE_INTERVAL_SECONDS",
    "ACES_OPERATION_RECORD_RETENTION_DAYS",
    "ACES_PACKAGE_BUCKET",
    "ACES_PACKAGE_MAX_ARCHIVE_BYTES",
    "ACES_PACKAGE_MAX_ENTRIES",
    "ACES_PACKAGE_MAX_UNCOMPRESSED_BYTES",
    "ACES_PACKAGE_PREFIX",
    "ACES_PACKAGE_ROOT",
]

_SLUG_RE = re.compile(r"^[-a-zA-Z0-9_]+\Z")
# Catalog ids are Django SlugFields capped at 100 (AcesPackageSource.scenario_id,
# ScenarioMetadata.scenario_id); route ids must fit the same bound.
_MAX_SCENARIO_ID_LEN = 100


def _parse_strict_bool(raw: str, *, name: str) -> bool:
    """Parse a strict boolean env value; unrecognized values fail closed.

    ``true/1/yes/on`` and ``false/0/no/off`` (case-insensitive, plus the empty
    unset default) are accepted; anything else raises ``ImproperlyConfigured`` so
    a typo never silently disables a capability.
    """
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off", ""}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean (true/false)")


def _parse_catalog_cutovers(raw: str, *, native_enabled: bool) -> Mapping[str, str]:
    """Parse ``SHIFTER_ACES_CATALOG_CUTOVERS`` into an immutable public->source map.

    Grammar: comma-separated ``public=source`` slug pairs. Strict -- exactly one
    ``=`` per pair, bounded non-empty Django-compatible slugs, each public mapped
    to a *distinct* source, unique public and source ids, and no ignored or
    last-wins entries. A non-empty mapping requires the ACES-native capability
    (the fail-closed two-key posture). Malformed syntax or the invalid
    non-empty-route/native-disabled combination raises ``ImproperlyConfigured``
    without echoing arbitrary input. The empty mapping is the legacy/rollback
    posture. Database/package resolvability is enforced later at registry
    resolution and readiness, never here at import.
    """
    text = raw.strip()
    if not text:
        return MappingProxyType({})
    mapping: dict[str, str] = {}
    seen_sources: set[str] = set()
    for segment in text.split(","):
        pair = segment.strip()
        if not pair or pair.count("=") != 1:
            raise ImproperlyConfigured("SHIFTER_ACES_CATALOG_CUTOVERS must be comma-separated public=source pairs")
        public_id, source_id = (part.strip() for part in pair.split("="))
        for slug in (public_id, source_id):
            if not slug or len(slug) > _MAX_SCENARIO_ID_LEN or _SLUG_RE.match(slug) is None:
                raise ImproperlyConfigured("SHIFTER_ACES_CATALOG_CUTOVERS ids must be bounded, non-empty slugs")
        if public_id == source_id:
            raise ImproperlyConfigured("SHIFTER_ACES_CATALOG_CUTOVERS must map a public id to a distinct source id")
        if public_id in mapping:
            raise ImproperlyConfigured("SHIFTER_ACES_CATALOG_CUTOVERS has a duplicate public id")
        if source_id in seen_sources:
            raise ImproperlyConfigured("SHIFTER_ACES_CATALOG_CUTOVERS has a duplicate source id")
        mapping[public_id] = source_id
        seen_sources.add(source_id)
    if not native_enabled:
        raise ImproperlyConfigured(
            "SHIFTER_ACES_CATALOG_CUTOVERS is non-empty but SHIFTER_ACES_NATIVE_PROVISIONING is disabled"
        )
    return MappingProxyType(mapping)


# Master capability/rollback gate for the ACES-native provisioning path
# (ADR-031-R2). While it is the temporary rollback switch it is NOT source
# precedence: which public id resolves to ACES is owned by the catalog
# source-route selector below and the registry. Disabling it prevents new ACES
# launches and leaves the cyberscript scenario -> RangeSpec -> hydrate ->
# interpret -> provisioner path authoritative, but must not disable status or
# teardown for already-persisted ACES ranges. Read via the literal os.environ.get
# form so config/env-manifest.json picks it up automatically.
ACES_NATIVE_PROVISIONING_ENABLED = _parse_strict_bool(
    os.environ.get("SHIFTER_ACES_NATIVE_PROVISIONING", "False"),
    name="SHIFTER_ACES_NATIVE_PROVISIONING",
)

# Catalog source-route selector for the ADR-024 default cutover (ADR-031-R6): a
# validated, immutable mapping from a stable public scenario id to a distinct
# registered ACES package-source id (e.g. ``polaris=polaris-aces``). The empty
# mapping is the preserved legacy/rollback posture. The registry owns resolution
# (fail-closed on dangling/non-conformant/duplicate targets); this setting only
# parses and validates the selector grammar and the two-key posture. Read via the
# literal os.environ.get form so config/env-manifest.json picks it up.
ACES_CATALOG_CUTOVERS = _parse_catalog_cutovers(
    os.environ.get("SHIFTER_ACES_CATALOG_CUTOVERS", ""),
    native_enabled=ACES_NATIVE_PROVISIONING_ENABLED,
)

# Filesystem root under which an ACES package_ref is resolved to its pack root by
# registration and the native launch loader (#1479, #1578). Repo-relative pack
# roots are joined to this setting with containment enforcement; launch verifies
# their canonical content digest before selecting the single direct SDL entry.
# Defaults to
# the repo root so in-repo scenario packages (e.g. scenario-dev/...) resolve out
# of the box; override per environment when packages live elsewhere. Read via the
# literal os.environ.get form so config/env-manifest.json picks it up.
# In the source tree config/ sits at shifter/shifter_platform/config, so
# parents[3] is the repo root (where in-repo scenario packages like scenario-dev/
# live). In the deployed container the tree is flattened to /app, so parents[3]
# does not exist; fall back to the app root instead of raising IndexError at
# import (which would break every image build / settings load). The default is
# only a starting point anyway — override SHIFTER_ACES_PACKAGE_ROOT per env.
_aces_settings_parents = Path(__file__).resolve().parents
_aces_default_package_root = _aces_settings_parents[3] if len(_aces_settings_parents) > 3 else _aces_settings_parents[1]
ACES_PACKAGE_ROOT = os.environ.get("SHIFTER_ACES_PACKAGE_ROOT", str(_aces_default_package_root))

# Object-storage location for object-backed ACES packages (#1567, ADR-034-R5).
# An ``object`` source row's ``package_ref`` names a single immutable archive
# object; the native launch resolver downloads it from this bucket (optionally
# under a fixed key prefix), safely extracts it into a private temp dir, and
# verifies its canonical content digest before SDL resolution or dispatch. An
# empty bucket keeps object-backed packs non-launchable (fail closed) — object
# launchability requires this to be configured. Read via the literal
# os.environ.get form so config/env-manifest.json picks it up.
ACES_PACKAGE_BUCKET = os.environ.get("SHIFTER_ACES_PACKAGE_BUCKET", "")
ACES_PACKAGE_PREFIX = os.environ.get("SHIFTER_ACES_PACKAGE_PREFIX", "")

# Fail-closed bounds for object-backed package retrieval and extraction (defense
# in depth against oversized downloads and archive bombs). Non-secret integers;
# override per environment. Defaults: 256 MiB archive, 1 GiB total uncompressed,
# 20000 members.
ACES_PACKAGE_MAX_ARCHIVE_BYTES = int(os.environ.get("SHIFTER_ACES_PACKAGE_MAX_ARCHIVE_BYTES", "268435456"))
ACES_PACKAGE_MAX_UNCOMPRESSED_BYTES = int(os.environ.get("SHIFTER_ACES_PACKAGE_MAX_UNCOMPRESSED_BYTES", "1073741824"))
ACES_PACKAGE_MAX_ENTRIES = int(os.environ.get("SHIFTER_ACES_PACKAGE_MAX_ENTRIES", "20000"))

# Days a runtime snapshot / operation-record row is retained before it becomes
# eligible for pruning. Measured from the row's source_timestamp so idempotent
# replay is deterministic. Non-positive disables the expiry stamp (never pruned).
ACES_OPERATION_RECORD_RETENTION_DAYS = int(os.environ.get("ACES_OPERATION_RECORD_RETENTION_DAYS", "30"))
# Cadence and bounded batch size for the dedicated ACES operation-record pruning
# service (run_aces_operation_record_prune). Non-secret integers.
ACES_OPERATION_RECORD_PRUNE_INTERVAL_SECONDS = int(
    os.environ.get("ACES_OPERATION_RECORD_PRUNE_INTERVAL_SECONDS", "3600")
)
ACES_OPERATION_RECORD_PRUNE_BATCH_SIZE = int(os.environ.get("ACES_OPERATION_RECORD_PRUNE_BATCH_SIZE", "500"))
