"""Scenario catalog projection construction.

Builds the :class:`~shared.schemas.cms_projections.ScenarioProjection` entry for
a validated scenario template (demo or CTF). Split out of
``cms.scenarios.registry`` so that module stays within the file-size limit while
the projection-shape logic lives in one cohesive place. RAES package-source rows
are projected by ``registry._raes_source_to_dict``; both feed the unified
catalog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cms.scenarios.schema import ScenarioTemplate

if TYPE_CHECKING:
    from cms.scenarios.schema import AnyScenarioTemplate
    from shared.schemas.cms_projections import ScenarioProjection


def _scenario_to_dict(
    template: AnyScenarioTemplate,
    *,
    is_default: bool,
    metadata: dict[str, Any] | None,
) -> ScenarioProjection:
    """Convert a ScenarioTemplate to a projection dict with metadata overlay.

    Builds a :class:`~shared.schemas.cms_projections.ScenarioProjection`: the
    common catalog metadata plus the source's authoring fields. Each template
    branch is constructed explicitly (rather than annotating ``model_dump()``,
    which is ``dict[str, Any]`` and not assignable to the TypedDict) so every
    key currently produced is preserved and mypy checks the shape without a cast.

    Args:
        template: Validated scenario template (demo or CTF).
        is_default: Whether this is a YAML-based default.
        metadata: Override dict with enabled/staff_only, or None for defaults.

    Returns:
        A ``ScenarioProjection`` with common metadata plus the template's
        authoring fields, ``is_default``, ``enabled``, ``staff_only``,
        ``launchable``, and ``agent_requirements``.
    """
    data = template.model_dump()

    # Apply metadata overlay (defaults: enabled from the template, staff_only=False).
    if metadata is not None:
        enabled = metadata["enabled"]
        staff_only = metadata.get("staff_only", False)
    else:
        enabled = data["enabled"]
        staff_only = False

    # Legacy YAML defaults and DB custom scenarios have always been launchable;
    # expose it as an explicit, uniform flag so launch consumers can filter on it.
    if isinstance(template, ScenarioTemplate):
        return {
            "id": data["id"],
            "name": data["name"],
            "description": data["description"],
            "scenario_type": data["scenario_type"],
            "enabled": enabled,
            "staff_only": staff_only,
            "is_default": is_default,
            "launchable": True,
            "agent_requirements": template.get_agent_requirements(),
            "ngfw": data["ngfw"],
            "instances": data["instances"],
            "subnets": data["subnets"],
            "participant_access": data["participant_access"],
        }
    # CTFScenarioTemplate: RAES/CTF authoring content carries no fixed-OS agent
    # requirement, matching the previous default-dict behavior.
    return {
        "id": data["id"],
        "name": data["name"],
        "description": data["description"],
        "scenario_type": data["scenario_type"],
        "enabled": enabled,
        "staff_only": staff_only,
        "is_default": is_default,
        "launchable": True,
        "agent_requirements": {
            "requires_windows": False,
            "requires_linux": False,
            "has_from_agent": False,
        },
        "cyberscript_version": data["cyberscript_version"],
        "zones": data["zones"],
        "networks": data["networks"],
        "forests": data["forests"],
        "services": data["services"],
        "assets": data["assets"],
        "flags": data["flags"],
        "data_seeds": data["data_seeds"],
        "detection": data["detection"],
        "participant_access": data["participant_access"],
    }
