"""Tenant-managed ACES image registry service (ADR-032-R2).

The single validated write path for :class:`engine.models.AcesImageMapping` -- the
tenant operator surface (Django admin) and any future management API both go
through this, so validation and idempotent upsert-by-natural-key live in one
place. The provisioner reads and resolves these rows at realization; it does not
use this seam (CQRS: the platform writes, the provisioner reads).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.models import AcesImageMapping

__all__ = ["AcesImageMappingError", "upsert_aces_image_mapping"]


class AcesImageMappingError(ValueError):
    """Raised when an ACES image mapping fails validation."""


def upsert_aces_image_mapping(
    *,
    provider: str,
    source_name: str,
    image_ref: str,
    source_version: str = "",
    machine_type: str = "",
    disk_size_gb: int | None = None,
    disk_type: str = "",
    enabled: bool = True,
    notes: str = "",
) -> AcesImageMapping:
    """Create or update an ACES image mapping, keyed on (provider, source_name, source_version).

    Idempotent so tenant automation can converge the registry declaratively. A
    blank ``source_version`` registers the any-version fallback. Retire a mapping
    by upserting with ``enabled=False`` (preserves audit; realization then fails
    loud) rather than deleting it.
    """
    from engine.models import AcesImageMapping

    normalized_provider = _normalize_provider(provider)
    name = _require(source_name, field="source_name")
    ref = _require(image_ref, field="image_ref")
    version = (source_version or "").strip()
    if disk_size_gb is not None and disk_size_gb <= 0:
        raise AcesImageMappingError("disk_size_gb must be a positive integer when set")

    mapping, _created = AcesImageMapping.objects.update_or_create(
        provider=normalized_provider,
        source_name=name,
        source_version=version,
        defaults={
            "image_ref": ref,
            "machine_type": (machine_type or "").strip(),
            "disk_size_gb": disk_size_gb,
            "disk_type": (disk_type or "").strip(),
            "enabled": enabled,
            "notes": notes or "",
        },
    )
    return mapping


def _normalize_provider(provider: str) -> str:
    from engine.models import AcesImageMapping

    value = (provider or "").strip().lower()
    valid = {choice for choice, _label in AcesImageMapping.Provider.choices}
    if value not in valid:
        raise AcesImageMappingError(f"provider must be one of {sorted(valid)}")
    return value


def _require(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcesImageMappingError(f"{field} must be a non-empty string")
    return value.strip()
