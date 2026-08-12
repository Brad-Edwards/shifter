"""Catalog source-route resolution for the ADR-024 default cutover (#1310).

The registry exclusively owns the mapping from a stable public scenario id to a
distinct registered RAES package-source id (ADR-031-R6). This module reads the
validated ``settings.RAES_CATALOG_CUTOVERS`` selector (the pure route table) and
turns it into one resolution that BOTH the catalog projection and
``create_range_dispatch`` consume, so listing and launch can never disagree.

It lives in its own module (like ``legacy_ids``) so the registry stays focused on
the unified projection. It holds no database query at import time; the one
resolution that needs the catalog imports ``get_catalog_entry`` lazily to avoid
an import cycle with the registry.

Fail-closed posture (ADR-031-R6): while a public id is routed, its internal
source id is never offered as a second launch choice, and a route whose target
is absent, not a registered RAES source, or non-conformant makes the public id
non-launchable rather than silently falling back to the legacy path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from shared.schemas.cms_projections import AgentRequirements, ScenarioProjection

# RAES catalog entries carry no cyberscript agent requirements.
_RAES_AGENT_REQUIREMENTS: AgentRequirements = {
    "requires_windows": False,
    "requires_linux": False,
    "has_from_agent": False,
}


def active_cutover_map() -> Mapping[str, str]:
    """Return the validated ``public_id -> source_id`` route mapping.

    Empty is the preserved legacy/rollback posture. The mapping was parsed and
    validated at settings import (:mod:`config._raes_settings`); this is only the
    read seam so callers never touch ``settings`` directly.
    """
    return getattr(settings, "RAES_CATALOG_CUTOVERS", {}) or {}


def resolve_cutover_source_id(public_id: str) -> str | None:
    """Return the internal RAES source id a public id routes to, or None if unrouted."""
    return active_cutover_map().get(public_id)


def routed_source_ids() -> frozenset[str]:
    """Return the internal source ids currently claimed by a route.

    A claimed source id is never offered as a second, direct launch choice while
    its public id is routed to it.
    """
    return frozenset(active_cutover_map().values())


def apply_cutover_routes(entries: list[ScenarioProjection]) -> list[ScenarioProjection]:
    """Overlay the ADR-031-R6 source routes onto the unified catalog projection.

    For each active ``public_id -> source_id`` route the public entry becomes the
    RAES-backed launch choice -- inheriting the target source's launchability and
    RAES fields while keeping its public id, display, and ``ScenarioMetadata``
    access overlay -- and the internal source id is suppressed as a second launch
    choice. A route whose target is absent, not a registered RAES source, or
    non-launchable fails closed: the public id is marked non-launchable rather
    than silently launching legacy. With an empty route map the projection is
    returned unchanged.
    """
    route_map = active_cutover_map()
    if not route_map:
        return entries
    by_id = {entry["id"]: entry for entry in entries}
    suppressed = set(route_map.values())
    overlaid: list[ScenarioProjection] = []
    for entry in entries:
        entry_id = entry["id"]
        if entry_id in suppressed:
            # A routed internal source id is not offered as a second launch choice.
            continue
        source_id = route_map.get(entry_id)
        if source_id is None:
            overlaid.append(entry)
            continue
        overlaid.append(_overlay_raes_route(entry, by_id.get(source_id)))
    return overlaid


def _overlay_raes_route(
    public_entry: ScenarioProjection, source_entry: ScenarioProjection | None
) -> ScenarioProjection:
    """Return the public entry re-backed by its routed RAES source (fail-closed)."""
    overlaid = public_entry.copy()
    overlaid["scenario_type"] = "raes"
    if source_entry is None or source_entry.get("scenario_type") != "raes":
        # Routed target absent, not visible, or not a registered RAES source ->
        # fail closed. A route to an existing legacy catalog id (e.g. polaris=basic)
        # has no backing RaesPackageSource, so advertising it launchable would
        # promise a launch that necessarily fails at dispatch; treat it exactly
        # like a missing target rather than copying legacy launchability (ADR-031-R6).
        overlaid["launchable"] = False
        overlaid["source_kind"] = ""
        overlaid["contract_kind"] = ""
        overlaid["contract_profile"] = ""
        overlaid["agent_requirements"] = _RAES_AGENT_REQUIREMENTS.copy()
        return overlaid
    overlaid["launchable"] = bool(source_entry.get("launchable", False))
    overlaid["source_kind"] = source_entry.get("source_kind", "")
    overlaid["contract_kind"] = source_entry.get("contract_kind", "")
    overlaid["contract_profile"] = source_entry.get("contract_profile", "")
    overlaid["agent_requirements"] = source_entry.get("agent_requirements", _RAES_AGENT_REQUIREMENTS.copy())
    return overlaid


@dataclass(frozen=True)
class LaunchResolution:
    """One resolved launch decision, consumed by both projection and dispatch.

    ``scenario_id`` is the stable public id to persist/correlate; ``raes_source_id``
    is the internal RAES package-source to load (``None`` for a legacy launch or a
    suppressed target); ``is_raes`` marks the RAES-native path; ``launchable``
    reflects the fail-closed catalog decision.
    """

    scenario_id: str
    raes_source_id: str | None
    is_raes: bool
    launchable: bool


def resolve_launch(scenario_id: str) -> LaunchResolution:
    """Resolve one scenario id to its launch route (ADR-031-R5/R6).

    The single resolution both the catalog projection and ``create_range_dispatch``
    consume, so listing and launch never disagree. A routed public id resolves to
    its internal RAES source; a routed internal source id is not a direct launch
    choice; an unrouted registered RAES source launches directly; everything else
    is legacy. Launchability reflects the fail-closed catalog entry (an
    unresolved/non-conformant RAES route is not launchable).
    """
    source_id = resolve_cutover_source_id(scenario_id)
    if source_id is not None:
        return _resolve_routed_public(scenario_id, source_id)
    if scenario_id in routed_source_ids():
        # A routed internal source id is not offered as a direct launch choice.
        return LaunchResolution(scenario_id, None, is_raes=True, launchable=False)
    return _resolve_unrouted(scenario_id)


def _resolve_routed_public(scenario_id: str, source_id: str) -> LaunchResolution:
    """Resolve a routed public id to its distinct internal RAES source (fail-closed launchability)."""
    from cms.scenarios.registry import get_catalog_entry

    entry = get_catalog_entry(scenario_id)
    return LaunchResolution(scenario_id, source_id, is_raes=True, launchable=bool(entry and entry.get("launchable")))


def _resolve_unrouted(scenario_id: str) -> LaunchResolution:
    """Resolve an unrouted id: a registered RAES source launches directly; everything else is legacy."""
    from cms.scenarios.registry import get_catalog_entry

    entry = get_catalog_entry(scenario_id)
    if entry is not None and entry.get("scenario_type") == "raes":
        return LaunchResolution(scenario_id, scenario_id, is_raes=True, launchable=bool(entry.get("launchable")))
    launchable = bool(entry.get("launchable", True)) if entry is not None else False
    return LaunchResolution(scenario_id, None, is_raes=False, launchable=launchable)
