"""Cross-backend installation boundary for model-access policy (PLAT-202).

The semantic models live in ``shared.model_access``.  This independently
packaged installer consumes their generated JSON Schema and publishes the
validated catalog as a mounted artifact; only its fixed path and digest enter
the runtime environment.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from shared.model_access import ContractError, seal_catalog, validate_catalog

from .errors import ConfigIssue

SETTINGS_KEY = "model_access"
DEFAULT_CATALOG_PATH = "/etc/shifter/model-access/catalog.json"
_SCHEMA_PATH = Path(__file__).with_name("published_contract") / "model-access-policy.v1.schema.json"
_ENVELOPE_KEYS = frozenset({"enabled", "catalog"})


def compute_catalog_digest(catalog: Mapping[str, Any]) -> str:
    """Compute the v1 digest after canonical semantic validation and normalization."""
    semantic = dict(catalog)
    semantic.pop("digest", None)
    return seal_catalog(semantic).digest


def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _issue_path(parts: list[object]) -> str:
    suffix = ".".join(str(item) for item in parts)
    return f"settings.{SETTINGS_KEY}.catalog" + (f".{suffix}" if suffix else "")


def validate_settings_block(settings: Mapping[str, Any]) -> tuple[dict[str, Any], list[ConfigIssue]]:
    """Validate and normalize ``settings.model_access`` without exposing values."""
    normalized = dict(settings)
    raw = settings.get(SETTINGS_KEY)
    if raw is None:
        return normalized, []
    if not isinstance(raw, Mapping):
        return normalized, [ConfigIssue(f"settings.{SETTINGS_KEY}", "must be a mapping")]
    unknown = sorted(set(raw) - _ENVELOPE_KEYS)
    if unknown:
        return normalized, [ConfigIssue(f"settings.{SETTINGS_KEY}.{unknown[0]}", "unknown field")]
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        return normalized, [ConfigIssue(f"settings.{SETTINGS_KEY}.enabled", "must be a boolean")]
    catalog = raw.get("catalog")
    if enabled and catalog is None:
        return normalized, [ConfigIssue(f"settings.{SETTINGS_KEY}.catalog", "is required when enabled")]
    if catalog is None:
        normalized[SETTINGS_KEY] = {"enabled": enabled, "catalog": None}
        return normalized, []
    if not isinstance(catalog, Mapping):
        return normalized, [ConfigIssue(f"settings.{SETTINGS_KEY}.catalog", "must be a mapping")]
    errors = sorted(Draft202012Validator(_schema()).iter_errors(catalog), key=lambda item: list(item.absolute_path))
    if errors:
        return normalized, [
            ConfigIssue(_issue_path(list(error.absolute_path)), "failed the model-access contract schema")
            for error in errors
        ]
    try:
        canonical = validate_catalog(catalog)
    except ContractError as exc:
        path = f"settings.{SETTINGS_KEY}.catalog"
        if exc.path != "<root>":
            path = f"{path}.{exc.path}"
        return normalized, [ConfigIssue(path, f"failed semantic validation ({exc.code})")]
    normalized[SETTINGS_KEY] = {"enabled": enabled, "catalog": canonical.model_dump(mode="json")}
    return normalized, []
