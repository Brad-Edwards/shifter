"""Active legacy scenario ids — the no-shadow set for content ingestion (#1578).

The set of ``scenario_id`` values already taken by an active YAML-default or
DB-custom (legacy) scenario. The uniform content-ingestion path
(:func:`cms.services.register_pack`) rejects a pack whose ``scenario_id`` would
shadow one of these, preserving the registry's fail-closed no-shadow posture and
the ADR-024 cutover ordering (a new pack must never mask an active legacy
scenario).

This lives in its own module rather than in ``cms.scenarios.registry`` so the
registry stays focused on the unified catalog projection; the no-shadow set is a
distinct, small concern consumed by ingestion.
"""

from __future__ import annotations

from cms.scenarios.loader import get_all_scenarios as get_yaml_scenarios


def active_legacy_scenario_ids() -> set[str]:
    """Return the active YAML-default and DB-custom scenario ids (the no-shadow set).

    A ``scenario_id`` is "taken" the moment a legacy scenario uses it, so the set
    is every YAML default id plus every active DB ``Scenario`` id (soft-deleted
    rows are excluded by the model's default manager). Both surfaces enforce a
    unique id, so a pack reusing any of them would shadow live content.
    """
    from cms.models import Scenario

    yaml_ids = {template.id for template in get_yaml_scenarios()}
    db_ids = set(Scenario.objects.values_list("scenario_id", flat=True))
    return yaml_ids | db_ids
