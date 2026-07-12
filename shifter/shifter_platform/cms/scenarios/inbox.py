"""In-box scenario-catalog bootstrap (#1578, ADR-034).

The packs Shifter ships by default are *not* loaded through a privileged code
path. They are declared in a manifest and registered through the SAME
:func:`cms.services.register_pack` service an operator uses. This is the
dogfooding requirement of ADR-033/ADR-034: the shipped catalog and operator
content share one ingestion path.

There are no conformant default scenario packs yet (program #1584), so the
shipped manifest (:data:`SHIPPED_INBOX_MANIFEST`) declares an empty pack list.
The mechanism is in place and exercised by tests; entries are added as first-party
packs are authored.

Bootstrap is idempotent: an entry whose ``scenario_id`` is already registered is
skipped, so re-running it after a deploy is safe.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from cms.models import AcesPackageSource
from cms.services import PackRegistrationRequest, RegisteredPack, register_pack
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# The declared in-box pack manifest that ships with Shifter.
SHIPPED_INBOX_MANIFEST = Path(__file__).parent / "inbox_packs" / "manifest.yaml"


def load_inbox_manifest(manifest_path: Path | None = None) -> list[dict[str, Any]]:
    """Return the declared in-box pack entries (``[]`` when absent or empty)."""
    path = Path(manifest_path) if manifest_path is not None else SHIPPED_INBOX_MANIFEST
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    if not isinstance(doc, dict):
        return []
    packs = doc.get("packs") or []
    return [entry for entry in packs if isinstance(entry, dict)]


def register_inbox_packs(
    *, actor: User, manifest_path: Path | None = None, request_id: str = ""
) -> list[RegisteredPack]:
    """Register every declared in-box pack through the uniform ingestion service.

    Args:
        actor: The registering user (must pass the CMS authoring gate).
        manifest_path: Optional manifest override (defaults to the shipped one).
        request_id: Optional audit correlation id.

    Returns:
        The packs newly registered by this call (already-registered ids skipped).
    """
    registered: list[RegisteredPack] = []
    for entry in load_inbox_manifest(manifest_path):
        scenario_id = entry.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            logger.warning("inbox bootstrap: manifest entry missing scenario_id; skipping")
            continue
        if AcesPackageSource.objects.filter(scenario_id=scenario_id).exists():
            logger.info("inbox bootstrap: pack already registered; skipping id=%s", safe_log_value(scenario_id))
            continue
        registered.append(register_pack(user=actor, request=_entry_to_request(entry), request_id=request_id))
    return registered


def _entry_to_request(entry: dict[str, Any]) -> PackRegistrationRequest:
    """Build a registration request from a manifest entry (validation is downstream)."""
    return PackRegistrationRequest(
        scenario_id=entry["scenario_id"],
        source_kind=entry.get("source_kind", "repo"),
        contract_kind=entry.get("contract_kind", "aces"),
        contract_profile=entry.get("contract_profile", "shifter"),
        package_ref=entry.get("package_ref", ""),
        package_version=entry.get("package_version", ""),
        package_digest=entry.get("package_digest", ""),
        lock_ref=entry.get("lock_ref", ""),
        lock_digest=entry.get("lock_digest", ""),
        provenance=entry.get("provenance") or {},
    )
