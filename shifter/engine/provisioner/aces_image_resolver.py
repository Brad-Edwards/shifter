"""Pure resolver for the tenant-managed ACES image registry (ADR-032-R2).

The registry (``engine_aces_image_mapping``, read via
``provisioner_db.get_aces_image_candidates``) maps an authored ACES image
identity (``source`` name + optional version) to a concrete provider image.
This module holds the *pure* matching rules -- exact ``(name, version)`` match,
then the any-version fallback (blank ``source_version``) -- separate from DB
access so they are trivially testable and live in exactly one place. Passthrough
of an already-concrete image ref and fail-loud on no match are the backend
realization policy (applied by the provider-specific builder), because "already
concrete" is provider-specific.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedImage:
    """A concrete provider image resolved from the registry (ADR-032-R2)."""

    image_ref: str
    machine_type: str | None = None
    disk_size_gb: int | None = None
    disk_type: str | None = None


def resolve_from_candidates(candidates: Sequence[dict[str, Any]], *, version: str | None) -> ResolvedImage | None:
    """Resolve a registry match from candidate rows for one (provider, source_name).

    Prefers an exact ``source_version`` match, then the any-version fallback (a
    row whose ``source_version`` is blank). Returns ``None`` when neither exists,
    leaving passthrough / fail-loud to the caller.
    """
    wanted = (version or "").strip()
    exact: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    for candidate in candidates:
        candidate_version = (candidate.get("source_version") or "").strip()
        if candidate_version == wanted and exact is None:
            exact = candidate
        elif candidate_version == "" and fallback is None:
            fallback = candidate

    chosen = exact or fallback
    if chosen is None:
        return None
    return ResolvedImage(
        image_ref=chosen["image_ref"],
        machine_type=(chosen.get("machine_type") or "") or None,
        disk_size_gb=chosen.get("disk_size_gb"),
        disk_type=(chosen.get("disk_type") or "") or None,
    )
