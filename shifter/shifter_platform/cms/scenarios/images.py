"""Scenario image projection for capacity pre-bake planning (PLAT-201, #680).

CMS owns scenario hydration, so CMS is where a scenario is turned into the
bounded image identities one range needs. The Engine consumes this projection
rather than re-parsing scenario content or building a parallel AMI mapping.

Two scenario shapes are supported because the platform has two: legacy
CyberScript templates carry ``instances``, and CTF templates carry ``assets``.
The CTF ``scope`` field is load-bearing here -- an asset declared ``shared``
exists once for the whole event, so scaling it by concurrent ranges would
overstate pre-bake demand by roughly the size of the cohort.

Only identity and counts cross this boundary: never authored services, flags,
data seeds, domain configuration, or any other scenario payload. A scenario
that cannot be read yields an empty, explicitly unresolved projection rather
than a fabricated one -- consistent with the rest of the capacity layer, where
"we could not determine this" is never rendered as a satisfied answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cms.scenarios.registry import load_scenario_template as load_scenario
from shared.capacity import ImageCount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScenarioImageProjection:
    """Image identities one range needs, split by how they scale.

    ``per_range`` scales with the number of concurrent ranges. ``shared`` is
    realized once for the event regardless of cohort size. ``resolved`` is False
    when the scenario could not be read or carried no recognizable node shape.
    """

    per_range: tuple[ImageCount, ...] = ()
    shared: tuple[ImageCount, ...] = ()
    resolved: bool = False

    def as_hint(self) -> dict[str, Any]:
        """Return the JSON-safe form carried inside a capacity declaration."""
        return {
            "resolved": self.resolved,
            "per_range": [_as_entry(image) for image in self.per_range],
            "shared": [_as_entry(image) for image in self.shared],
        }


def _as_entry(image: ImageCount) -> dict[str, Any]:
    """Project one image count into its bounded wire form."""
    return {
        "source_name": image.source_name,
        "source_version": image.source_version,
        "os_family": image.os_family,
        "count": image.count,
    }


def project_scenario_images(scenario_id: str) -> ScenarioImageProjection:
    """Return the image identities one range of ``scenario_id`` needs.

    Never raises: an unknown or unreadable scenario yields an unresolved,
    empty projection so a capacity assessment degrades to "no per-image demand"
    rather than to a wrong pre-bake number.
    """
    try:
        template = load_scenario(scenario_id)
    except Exception:
        logger.warning("capacity: could not load scenario for image projection")
        return ScenarioImageProjection()

    assets = getattr(template, "assets", None)
    if assets:
        return _project_ctf_assets(assets)

    instances = getattr(template, "instances", None)
    if instances:
        return _project_legacy_instances(instances)

    logger.warning("capacity: scenario carried no recognizable node shape for image projection")
    return ScenarioImageProjection()


def _project_legacy_instances(instances: Any) -> ScenarioImageProjection:
    """Project legacy CyberScript instances; every instance is per-range.

    A custom ``ami_key`` is a distinct image to pre-bake, so it becomes the
    image identity in place of the OS default.
    """
    tally: dict[tuple[str, str, str], int] = {}
    for instance in instances:
        os_type = str(getattr(instance, "os_type", "") or "")
        ami_key = str(getattr(instance, "ami_key", "") or "")
        source_name = ami_key or os_type
        if not source_name:
            continue
        key = (source_name, "", os_type)
        tally[key] = tally.get(key, 0) + 1

    return ScenarioImageProjection(per_range=_to_counts(tally), resolved=bool(tally))


def _project_ctf_assets(assets: Any) -> ScenarioImageProjection:
    """Project CTF assets, splitting per-participant from event-shared."""
    per_range: dict[tuple[str, str, str], int] = {}
    shared: dict[tuple[str, str, str], int] = {}

    for asset in assets:
        os_type = str(getattr(asset, "os_type", "") or "")
        image = str(getattr(asset, "image", "") or "")
        source_name = image or os_type
        if not source_name:
            continue
        key = (source_name, "", os_type)
        bucket = shared if str(getattr(asset, "scope", "per_participant")) == "shared" else per_range
        bucket[key] = bucket.get(key, 0) + 1

    resolved = bool(per_range or shared)
    return ScenarioImageProjection(
        per_range=_to_counts(per_range),
        shared=_to_counts(shared),
        resolved=resolved,
    )


def _to_counts(tally: dict[tuple[str, str, str], int]) -> tuple[ImageCount, ...]:
    """Turn an identity tally into a stable, sorted tuple of image counts."""
    return tuple(
        sorted(
            ImageCount(source_name=name, source_version=version, os_family=os_family, count=count)
            for (name, version, os_family), count in tally.items()
        )
    )
