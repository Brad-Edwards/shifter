"""Scenario template loader.

Loads and validates YAML scenario templates from cms/scenarios/templates/.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from cms.scenarios.schema import ScenarioTemplate
from shared.log_sanitize import safe_log_value

logger = logging.getLogger(__name__)

# Directory containing scenario YAML templates
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _resolve_template_path(scenario_id: str) -> Path:
    """Resolve the YAML template path for ``scenario_id`` within TEMPLATES_DIR.

    ``scenario_id`` is user-controlled, so the resolved path is confirmed to
    stay inside :data:`TEMPLATES_DIR`. Path-traversal attempts (``..``,
    absolute paths, embedded separators) resolve outside the base dir and are
    rejected with a ``ValueError`` matching the not-found contract.
    """
    base = TEMPLATES_DIR.resolve()
    candidate = (base / f"{scenario_id}.yaml").resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"Scenario '{scenario_id}' not found")
    return candidate


@lru_cache(maxsize=32)
def load_scenario(scenario_id: str) -> ScenarioTemplate:
    """Load a scenario template by ID.

    Args:
        scenario_id: Unique scenario identifier (e.g., 'basic', 'ad_attack_lab')

    Returns:
        ScenarioTemplate: Validated scenario template

    Raises:
        ValueError: If scenario not found or template is invalid
    """
    logger.debug("load_scenario: scenario_id=%s", safe_log_value(scenario_id))

    template_path = _resolve_template_path(scenario_id)

    if not template_path.exists():
        logger.warning("load_scenario: not found scenario_id=%s", safe_log_value(scenario_id))
        raise ValueError(f"Scenario '{scenario_id}' not found")

    with open(template_path) as f:
        data = yaml.safe_load(f)

    logger.debug("load_scenario: loaded scenario_id=%s", safe_log_value(scenario_id))
    return ScenarioTemplate(**data)


def list_scenario_ids() -> list[str]:
    """List all available scenario IDs.

    Returns:
        List of scenario IDs (derived from YAML filenames)
    """
    if not TEMPLATES_DIR.exists():
        logger.warning("list_scenario_ids: templates directory not found")
        return []

    ids = sorted([path.stem for path in TEMPLATES_DIR.glob("*.yaml")])
    logger.debug("list_scenario_ids: found %d scenarios", len(ids))
    return ids


def get_all_scenarios() -> list[ScenarioTemplate]:
    """Get all available scenarios.

    Returns:
        List of validated ScenarioTemplate objects
    """
    return [load_scenario(scenario_id) for scenario_id in list_scenario_ids()]
