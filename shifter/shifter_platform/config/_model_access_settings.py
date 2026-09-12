"""Fail-closed runtime binding for the mounted model-access catalog."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from shared.model_access import ContractError, ModelAccessCatalog, load_catalog_json

MAX_MODEL_ACCESS_CATALOG_BYTES = 2 * 1024 * 1024


def load_model_access_catalog(*, enabled: bool, path: str, expected_digest: str) -> ModelAccessCatalog | None:
    """Reparse a bounded mounted artifact and bind it to the rendered digest."""
    if not path and not expected_digest:
        if enabled:
            raise ImproperlyConfigured("enabled model access requires a catalog path and digest")
        return None
    if not path or not expected_digest:
        raise ImproperlyConfigured("model access catalog path and digest must be configured together")
    catalog_path = Path(path)
    try:
        if catalog_path.stat().st_size > MAX_MODEL_ACCESS_CATALOG_BYTES:
            raise ImproperlyConfigured("model access catalog exceeds the maximum size")
        raw = catalog_path.read_text(encoding="utf-8")
    except ImproperlyConfigured:
        raise
    except (OSError, UnicodeError) as exc:
        raise ImproperlyConfigured("model access catalog could not be read") from exc
    try:
        catalog = load_catalog_json(raw)
    except ContractError as exc:
        raise ImproperlyConfigured(f"model access catalog is invalid: {exc.code} at {exc.path}") from exc
    if not hmac.compare_digest(catalog.digest, expected_digest):
        raise ImproperlyConfigured("model access catalog does not match the expected digest")
    if enabled and not catalog.enabled:
        raise ImproperlyConfigured("runtime model access cannot enable a catalog declared disabled")
    return catalog


MODEL_ACCESS_ENABLED = os.environ.get("MODEL_ACCESS_ENABLED", "false").strip().lower() == "true"
MODEL_ACCESS_CATALOG_PATH = os.environ.get("MODEL_ACCESS_CATALOG_PATH", "").strip()
MODEL_ACCESS_CATALOG_DIGEST = os.environ.get("MODEL_ACCESS_CATALOG_DIGEST", "").strip()
MODEL_ACCESS_CATALOG = load_model_access_catalog(
    enabled=MODEL_ACCESS_ENABLED,
    path=MODEL_ACCESS_CATALOG_PATH,
    expected_digest=MODEL_ACCESS_CATALOG_DIGEST,
)

__all__ = [
    "MODEL_ACCESS_CATALOG",
    "MODEL_ACCESS_CATALOG_DIGEST",
    "MODEL_ACCESS_CATALOG_PATH",
    "MODEL_ACCESS_ENABLED",
]
