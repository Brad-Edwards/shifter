"""Scenario registry - unified access to YAML defaults and DB customs.

Merges scenario templates from two sources:
1. YAML files in cms/scenarios/templates/ (defaults, code-managed)
2. Scenario model instances in the database (staff-created customs)

Applies ScenarioMetadata overlays (enabled, staff_only) to all scenarios.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from cms.scenarios.loader import get_all_scenarios as get_yaml_scenarios
from cms.scenarios.loader import list_scenario_ids as list_yaml_ids
from cms.scenarios.loader import load_scenario as load_yaml_scenario
from cms.scenarios.schema import AnyScenarioTemplate, CTFScenarioTemplate, ScenarioTemplate
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def _get_metadata_map() -> dict[str, dict[str, Any]]:
    """Load all ScenarioMetadata rows as a dict keyed by scenario_id.

    Returns:
        {scenario_id: {"enabled": bool, "staff_only": bool}, ...}
    """
    from cms.models import ScenarioMetadata

    return {m.scenario_id: {"enabled": m.enabled, "staff_only": m.staff_only} for m in ScenarioMetadata.objects.all()}


def _get_db_scenarios() -> list[AnyScenarioTemplate]:
    """Load all active (non-deleted) custom scenarios from the database.

    Returns:
        List of ScenarioTemplate objects built from Scenario model instances.
    """
    from cms.models import Scenario

    scenarios = []
    for s in Scenario.objects.all():
        try:
            scenarios.append(s.to_template())
        except Exception:
            logger.warning(
                "Skipping invalid DB scenario: scenario_id=%s, id=%s",
                safe_log_value(s.scenario_id),
                s.id,
            )
    return scenarios


def _get_aces_sources() -> list[Any]:
    """Load all ACES package-source rows for the catalog projection.

    Returns:
        List of AcesPackageSource instances.
    """
    from cms.models import AcesPackageSource

    return list(AcesPackageSource.objects.all())


def _aces_source_to_dict(source: Any, *, metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Build a catalog projection entry for an ACES package-source row.

    ACES rows are provenance-only, so display fields are derived (name from
    scenario_id, empty description). Access comes from the shared
    ``ScenarioMetadata`` overlay; launchability is derived from conformance
    readiness and is independent of access.

    Args:
        source: AcesPackageSource instance.
        metadata: Override dict with enabled/staff_only, or None for defaults.

    Returns:
        Projection dict shaped like other catalog entries (id/name/enabled/
        staff_only/is_default/agent_requirements) plus ACES source fields.
    """
    if metadata is not None:
        enabled = metadata["enabled"]
        staff_only = metadata.get("staff_only", False)
    else:
        enabled = True
        staff_only = False

    return {
        "id": source.scenario_id,
        "name": source.scenario_id,
        "description": "",
        "scenario_type": "aces",
        "source_kind": source.source_kind,
        "contract_kind": source.contract_kind,
        "contract_profile": source.contract_profile,
        "is_default": False,
        "enabled": enabled,
        "staff_only": staff_only,
        "launchable": source.is_launchable,
        "agent_requirements": {
            "requires_windows": False,
            "requires_linux": False,
            "has_from_agent": False,
        },
    }


def is_default_scenario(scenario_id: str) -> bool:
    """Check if a scenario_id corresponds to a YAML default.

    Args:
        scenario_id: The scenario identifier to check.

    Returns:
        True if the scenario exists as a YAML file in templates/.
    """
    return scenario_id in list_yaml_ids()


def _scenario_to_dict(
    template: AnyScenarioTemplate,
    *,
    is_default: bool,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert a ScenarioTemplate to a dict with metadata overlay.

    Args:
        template: Validated scenario template.
        is_default: Whether this is a YAML-based default.
        metadata: Override dict with enabled/staff_only, or None for defaults.

    Returns:
        Dict with scenario fields plus is_default, enabled, staff_only,
        and agent_requirements.
    """
    data = template.model_dump()

    # Apply metadata overlay (defaults: enabled=True, staff_only=False)
    if metadata is not None:
        data["enabled"] = metadata["enabled"]
        data["staff_only"] = metadata.get("staff_only", False)
    else:
        # No metadata row — use template's own enabled flag, default staff_only
        data["staff_only"] = False

    data["is_default"] = is_default
    if isinstance(template, ScenarioTemplate):
        data["agent_requirements"] = template.get_agent_requirements()
    else:
        data["agent_requirements"] = {
            "requires_windows": False,
            "requires_linux": False,
            "has_from_agent": False,
        }
    return data


def list_all_scenarios(user: User | None = None) -> list[dict[str, Any]]:
    """Get all scenarios from both sources with metadata applied.

    Combines YAML defaults and DB customs, applies metadata overlays,
    and filters based on user role.

    Args:
        user: Requesting user. If None, returns all (no access filtering).
              If user is not staff, staff_only scenarios are excluded.
              Only enabled scenarios are returned for non-staff users.

    Returns:
        List of scenario dicts sorted by name.
    """
    metadata_map = _get_metadata_map()
    result = []

    # YAML defaults
    yaml_ids = set()
    for template in get_yaml_scenarios():
        yaml_ids.add(template.id)
        meta = metadata_map.get(template.id)
        entry = _scenario_to_dict(template, is_default=True, metadata=meta)
        result.append(entry)

    # DB customs (skip any whose scenario_id collides with a YAML default)
    db_ids = set()
    for template in _get_db_scenarios():
        if template.id in yaml_ids:
            logger.warning(
                "DB scenario '%s' collides with YAML default, skipping",
                template.id,
            )
            continue
        db_ids.add(template.id)
        meta = metadata_map.get(template.id)
        entry = _scenario_to_dict(template, is_default=False, metadata=meta)
        result.append(entry)

    # ACES package-source entries (fail-closed: skip any whose scenario_id
    # collides with an active legacy id — a YAML default or active DB custom —
    # so an ACES row can never shadow a launchable legacy scenario).
    known_ids = yaml_ids | db_ids
    for source in _get_aces_sources():
        if source.scenario_id in known_ids:
            logger.warning(
                "ACES package-source '%s' collides with an active legacy scenario_id, skipping",
                safe_log_value(source.scenario_id),
            )
            continue
        meta = metadata_map.get(source.scenario_id)
        result.append(_aces_source_to_dict(source, metadata=meta))

    # Access filtering
    if user is not None and not (user.is_staff or user.is_superuser):
        result = [s for s in result if s["enabled"] and not s["staff_only"]]

    # Sort by name
    result.sort(key=lambda s: s["name"])
    return result


def get_scenario_detail(scenario_id: str) -> dict[str, Any]:
    """Get a single scenario by ID from either source.

    Checks the database first, then falls back to YAML.

    Args:
        scenario_id: Unique scenario identifier.

    Returns:
        Scenario dict with metadata overlay.

    Raises:
        ValueError: If scenario not found in either source.
    """
    metadata_map = _get_metadata_map()
    meta = metadata_map.get(scenario_id)

    # Try database first
    from cms.models import Scenario

    try:
        db_scenario = Scenario.objects.get(scenario_id=scenario_id)
        template = db_scenario.to_template()
        return _scenario_to_dict(template, is_default=False, metadata=meta)
    except Scenario.DoesNotExist:
        pass

    # Fall back to YAML
    try:
        template = load_yaml_scenario(scenario_id)
        return _scenario_to_dict(template, is_default=True, metadata=meta)
    except ValueError as e:
        raise ValueError(f"Scenario '{scenario_id}' not found") from e


def load_demo_scenario_template(scenario_id: str) -> ScenarioTemplate:
    """Load a demo scenario template for hydration and agent-requirement checks."""
    template = load_scenario_template(scenario_id)
    if isinstance(template, CTFScenarioTemplate):
        raise ValueError(f"Scenario '{scenario_id}' is a CTF scenario")
    return template


def check_scenario_access(scenario_id: str, user: User) -> dict[str, Any]:
    """Check if a user can access a scenario. Returns detail dict or raises ValueError.

    Staff and superusers can access all scenarios. Non-staff users are blocked
    from disabled or staff_only scenarios.

    Args:
        scenario_id: Unique scenario identifier.
        user: The requesting user.

    Returns:
        Scenario detail dict (from get_scenario_detail).

    Raises:
        ValueError: If scenario not found or user lacks access.
    """
    detail = get_scenario_detail(scenario_id)
    if not (user.is_staff or user.is_superuser) and (not detail["enabled"] or detail["staff_only"]):
        raise ValueError(f"Scenario '{scenario_id}' is not available")
    return detail


def load_scenario_template(scenario_id: str) -> AnyScenarioTemplate:
    """Load a ScenarioTemplate from either source for hydration.

    This is the replacement for loader.load_scenario() that checks
    the database first.

    Args:
        scenario_id: Unique scenario identifier.

    Returns:
        Validated scenario template (demo or CTF).

    Raises:
        ValueError: If scenario not found in either source.
    """
    # Try database first
    from cms.models import Scenario

    try:
        db_scenario = Scenario.objects.get(scenario_id=scenario_id)
        return db_scenario.to_template()
    except Scenario.DoesNotExist:
        pass

    # Fall back to YAML
    return load_yaml_scenario(scenario_id)
