"""Static-typing contracts for ``cms.services`` projection dictionaries.

These ``TypedDict`` schemas describe the *shape* of the plain dictionaries
returned by the read-side ``cms.services`` entrypoints — ``list_agents``,
``initiate_upload``, ``list_scenarios`` / ``list_launchable_scenarios``, and
``get_scenario`` — and consumed by Mission Control views/serializers, the CTF
bridges, and Django templates.

They are **static typing only** (issue #317). The returned values have already
crossed their authoritative runtime boundaries — the ORM and
``_assert_agent_projection_shape`` for agents, HMAC upload-token signing and
S3 object/header inspection for uploads, and ``yaml.safe_load`` + Pydantic +
RAES source validation for scenarios. A ``TypedDict`` annotation is not a
validator, an authorization check, a serializer, or a response allowlist; every
existing runtime gate remains authoritative. See
``docs/architecture/typed-cms-service-projections-preflight-317.md``.

This module is shared-native and stdlib-only: it must not import ``cms`` or
CyberScript, and it does not re-model the Pydantic authoring schemas in
``cms.scenarios.schema``. Source-specific scenario authoring content is carried
as optional, JSON-shaped keys rather than a second hierarchy of TypedDicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from datetime import datetime


class AgentRequirements(TypedDict):
    """Agent-capability flags carried on every scenario projection."""

    requires_windows: bool
    requires_linux: bool
    has_from_agent: bool


class AgentListItem(TypedDict):
    """One row from ``cms.services.list_agents`` (``_agent_projection_dict``).

    Display fields only — never ``s3_key``, hashes, or owner identifiers. DRF's
    ``AgentListItemSerializer`` renders ``created_at`` as a string, but the
    service projection carries the ``datetime`` it read off the model.
    """

    id: int
    name: str
    os_name: str
    os_slug: str
    file_size_mb: float
    original_filename: str
    created_at: datetime
    agent_type: str
    agent_type_display: str


class UploadInitiation(TypedDict):
    """Result of ``cms.services.initiate_upload``.

    ``presigned_url`` and ``upload_token`` are short-lived bearer capabilities
    delivered only in the authenticated JSON response — never logged, persisted,
    or placed in argv/env. ``expected_os`` is non-null at the service boundary:
    installer initiation rejects a file format without an OS slug before the
    result is built, so the looser (nullable) DRF presentation schema does not
    weaken this invariant.
    """

    presigned_url: str
    s3_key: str
    upload_token: str
    expected_os: str


class ScenarioProjection(TypedDict):
    """One catalog entry from the scenario registry projection.

    The required keys are the common catalog metadata produced for every source
    (legacy demo templates, CTF templates, and RAES package sources). The
    optional keys are source-specific authoring/provenance content and are
    JSON-shaped rather than re-modelled from ``cms.scenarios.schema``.

    This is deliberately an overlay, not a discriminated union: cutover routing
    (``cms.scenarios.cutover.apply_cutover_routes``) can re-back a legacy-shaped
    entry with RAES, so ``scenario_type`` does not select one immutable shape.
    """

    # Common catalog metadata (every source).
    id: str
    name: str
    description: str
    scenario_type: str
    enabled: bool
    staff_only: bool
    is_default: bool
    launchable: bool
    agent_requirements: AgentRequirements

    # Legacy demo authoring content (``ScenarioTemplate``).
    ngfw: NotRequired[bool]
    instances: NotRequired[list[dict[str, Any]]]
    subnets: NotRequired[list[dict[str, Any]]]
    participant_access: NotRequired[list[dict[str, Any]] | dict[str, Any]]

    # CTF authoring content (``CTFScenarioTemplate``).
    cyberscript_version: NotRequired[str]
    zones: NotRequired[list[dict[str, Any]]]
    networks: NotRequired[list[dict[str, Any]]]
    forests: NotRequired[list[dict[str, Any]]]
    services: NotRequired[list[dict[str, Any]]]
    assets: NotRequired[list[dict[str, Any]]]
    flags: NotRequired[list[dict[str, Any]]]
    data_seeds: NotRequired[list[dict[str, Any]]]
    detection: NotRequired[dict[str, Any] | None]

    # RAES package-source provenance (``_raes_source_to_dict``).
    source_kind: NotRequired[str]
    contract_kind: NotRequired[str]
    contract_profile: NotRequired[str]
