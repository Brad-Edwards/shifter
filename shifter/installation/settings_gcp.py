"""Closed settings model for the GCP backend bundle (PLAT-2003, #729).

This is the operator-authored GCP intent carried under ``RootConfig.settings`` when
``backend: gcp`` — the deployment project and region, plus the cross-backend range
egress policy. It is the ``settings_model`` the ``gcp`` bundle registers
(:mod:`installation.registry`), so :meth:`installation.contract.BackendBundle.validate_settings`
validates a GCP ``shifter.yaml``'s ``settings`` block against it before any Terraform,
Helm, or cluster mutation.

The model is *closed* (``extra="forbid"``): an unknown GCP setting fails fast rather
than being silently ignored, which is the contract guarantee the backend-bundle design
relies on (ADR-011). ``project_id`` and ``region`` are constrained with
schema-expressible ``pattern`` / length bounds (not custom validators) so the exact same
grammar the loader enforces is also carried into the *published* settings JSON schema —
a contract consumer validating against the published schema cannot accept an identifier
``load_root_config`` would reject. ``range_egress`` **composes** the canonical
:class:`installation.range_egress.RangeEgressPolicy` — the provider-neutral egress shape
and its CIDR/mode validation live there (PLAT-220) and are reused here, never copied,
so AWS and GCP cannot drift into conflicting egress policy. The loader
(:mod:`installation.loader`) additionally runs the canonical range-egress pass so the
stored settings carry the same normalized form for every backend.

See ``docs/architecture/gcp-backend-bundle-migration-preflight-729.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .range_egress import RangeEgressPolicy

# GCP project id grammar: 6-30 characters, starting with a lowercase letter, then
# lowercase letters, digits, and hyphens, and not ending in a hyphen. This is Google's
# documented project-id rule; expressing it as a schema ``pattern`` (rather than a custom
# validator) turns a typo into a fail-fast config error AND publishes the constraint into
# the settings JSON schema so downstream contract consumers reject the same identifiers.
_GCP_PROJECT_ID_PATTERN = r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
# GCP region/location grammar: a lowercase DNS-label-ish token (letters, digits, internal
# hyphens), e.g. ``us-central1``, ``europe-west4``. Kept deliberately permissive over the
# exact region list, which changes as Google adds regions; the deploy tooling validates a
# region actually exists.
_GCP_REGION_PATTERN = r"^[a-z][a-z0-9-]*[a-z0-9]$"


class GcpBackendSettings(BaseModel):
    """Validated ``settings`` for the ``gcp`` backend bundle."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(
        pattern=_GCP_PROJECT_ID_PATTERN,
        min_length=6,
        max_length=30,
        description=(
            "GCP project id: 6-30 characters, a lowercase letter followed by lowercase letters, digits, "
            "and hyphens, not ending in a hyphen."
        ),
    )
    region: str = Field(
        pattern=_GCP_REGION_PATTERN,
        description="Lowercase GCP region/location token (letters, digits, and internal hyphens), e.g. 'us-central1'.",
    )
    range_egress: RangeEgressPolicy = Field(default_factory=RangeEgressPolicy)
