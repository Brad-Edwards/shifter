"""No-dispatch backend realizability assessment for ACES scenarios (ADR-034-R3).

ADR-034-R3 requires ingestion to validate realizability against the backend
manifest and surface non-realizability to the author without creating loopholes.
This module is the *capability* contributor to that answer: it runs the real ACES
compile/plan/validate path -- ``load_scenario`` -> ``RuntimeManager.plan`` ->
execution-plan diagnostics -> :meth:`ShifterProvisioner.validate` -- and projects
every out-of-envelope term into a bounded typed gap.

It deliberately stops before ``apply``. An authoring-time check must never
realize anything, so the backend target is built with :class:`_NeverDispatchPort`,
which raises on any ``realize()`` call: "assessment does not dispatch" is
enforced by construction rather than by convention. Nothing here touches the
database, object storage, a cloud API, a subprocess, or the guest.

This module answers only "does the declared capability envelope admit this
scenario". Backend *supply* (does a concrete image mapping exist for each node)
is a separate contributor assessed by the catalog layer, which owns the registry
read; both contribute to one ordered gap list through :class:`RealizabilityGap`.
The vocabulary lives here so both contributors speak it.

Realizability is not validity, conformance, ``enabled``, editability, audience,
``launchable``, or proof that a range was realized, and it never replaces the
launch-time digest, plan, image, or provisioner admission checks -- it is early
feedback in front of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aces_sdl.scenarios import ScenarioError, load_scenario

from shared.aces.runtime_target import ShifterProvisioner, create_shifter_backend_target

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from aces_contracts.diagnostics import Diagnostic

__all__ = [
    "CapabilityAssessment",
    "GapCategory",
    "ImageDemand",
    "RealizabilityGap",
    "RealizabilityOutcome",
    "assess_scenario_capability",
    "worst_outcome",
]

#: ACES plan resource type carrying a provisioned node.
_NODE_RESOURCE_TYPE = "node"

#: Gap code for a scenario that could not be compiled or planned at all.
UNASSESSABLE_SCENARIO_CODE = "shifter-realizability.scenario-unassessable"

#: Address used for gaps that belong to the plan as a whole rather than a resource.
_PLAN_ADDRESS = "plan"


class RealizabilityOutcome(StrEnum):
    """Closed outcome of a realizability assessment.

    ``INDETERMINATE`` means the assessment could not be completed (unreadable
    pack, unparseable SDL, an incomplete parameter binding). It is deliberately
    distinct from ``NOT_REALIZABLE``: one is a proven gap, the other is an
    inability to prove anything. Neither may ever be rendered or admitted as
    realizable.
    """

    REALIZABLE = "realizable"
    NOT_REALIZABLE = "not_realizable"
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"


class GapCategory(StrEnum):
    """Which contributor produced a gap, so the author can tell them apart."""

    CAPABILITY = "capability"
    IMAGE_SUPPLY = "image_supply"
    SOURCE_INTEGRITY = "source_integrity"
    TARGET = "target"


@dataclass(frozen=True, order=True)
class RealizabilityGap:
    """One bounded reason a scenario cannot be realized.

    Carries a stable ``code`` (clients switch on this, never on prose), the
    contributing ``category``, the ``address`` of the offending resource, and a
    safe authored ``message``. It never carries authored payloads, parameter or
    account values, provider detail, credentials, or local filesystem paths.
    """

    code: str
    address: str
    category: GapCategory
    message: str


@dataclass(frozen=True, order=True)
class ImageDemand:
    """One node's bounded image identity, for the backend-supply contributor.

    ``source_name`` is empty when the node declares no authored source: such a
    node still needs a boot OS, so it demands a base-OS mapping keyed on
    ``os_family`` (legitimate backend policy under ADR-032) rather than nothing.
    ``source_version`` is empty when the author left the version unpinned.

    Only identity crosses this boundary -- never authored resources, services,
    features, conditions, vulnerabilities, or any other node payload.
    """

    address: str
    source_name: str
    source_version: str
    os_family: str


@dataclass(frozen=True)
class CapabilityAssessment:
    """Capability-envelope half of a realizability answer.

    ``image_demands`` is carried alongside the outcome so the catalog layer can
    resolve backend supply from the same compile rather than re-parsing the
    pack's SDL. It is empty whenever the scenario could not be planned.
    """

    outcome: RealizabilityOutcome
    gaps: tuple[RealizabilityGap, ...] = ()
    image_demands: tuple[ImageDemand, ...] = ()


class _NeverDispatchPort:
    """Dispatch port that refuses to realize anything.

    ``RuntimeManager`` requires a provisioning backend target, and that target
    requires a dispatch port. Assessment must never reach it: this port turns a
    regression that reintroduces ``apply`` into a loud failure instead of a
    silently provisioned range.
    """

    @property
    def request_id(self) -> str:
        """Identify the assessment; no Shifter request is ever created."""
        return "realizability-assessment"

    def realize(self, compiled_plan: dict[str, Any]) -> Any:
        """Refuse to dispatch -- assessment is an authoring check, not a launch."""
        raise AssertionError("realizability assessment must never dispatch a provisioning plan")


def assess_scenario_capability(
    scenario_path: Path,
    *,
    parameters: Mapping[str, object] | None = None,
) -> CapabilityAssessment:
    """Assess whether the declared capability envelope admits this ACES scenario.

    Runs the real ACES compile/plan/validate path without applying it. Load,
    compile, and plan failures degrade to ``INDETERMINATE`` with one bounded gap
    rather than raising, because "we could not assess this" is an answer the
    editor must render -- but never as realizable.

    Args:
        scenario_path: Path to the pack's ``*.sdl.yaml`` start-state document.
        parameters: Optional parameter binding for a parameterized scenario.

    Returns:
        The capability half of the realizability answer.
    """
    try:
        scenario = load_scenario(scenario_path)
    except (ScenarioError, OSError, ValueError) as exc:
        return _indeterminate("scenario could not be loaded", exc)

    try:
        manager_plan = _plan(scenario, parameters)
    # Any planner failure means "cannot assess", not a crash: the editor must
    # render INDETERMINATE rather than 500, and must never call it realizable.
    except Exception as exc:
        return _indeterminate("scenario could not be planned", exc)

    diagnostics = [*manager_plan.diagnostics, *ShifterProvisioner.validate(manager_plan.provisioning)]
    gaps = _project_gaps(diagnostics)
    outcome = RealizabilityOutcome.NOT_REALIZABLE if gaps else RealizabilityOutcome.REALIZABLE
    return CapabilityAssessment(
        outcome=outcome,
        gaps=gaps,
        image_demands=_project_image_demands(manager_plan.provisioning),
    )


def worst_outcome(outcomes: Iterable[RealizabilityOutcome]) -> RealizabilityOutcome:
    """Combine contributor outcomes, keeping the least-permissive one.

    ``NOT_REALIZABLE`` beats ``INDETERMINATE`` beats ``REALIZABLE`` so no
    contributor can upgrade another's answer, and neither a proven gap nor an
    inability to assess can be masked by a passing contributor.
    """
    ranked = list(outcomes)
    for candidate in (
        RealizabilityOutcome.NOT_APPLICABLE,
        RealizabilityOutcome.NOT_REALIZABLE,
        RealizabilityOutcome.INDETERMINATE,
    ):
        if candidate in ranked:
            return candidate
    return RealizabilityOutcome.REALIZABLE


def _plan(scenario: object, parameters: Mapping[str, object] | None) -> Any:
    """Compile and plan ``scenario`` against the Shifter backend without applying."""
    from aces_runtime import RuntimeManager

    target = create_shifter_backend_target(port=_NeverDispatchPort())
    return RuntimeManager(target).plan(scenario, parameters=dict(parameters) if parameters else None)


def _project_gaps(diagnostics: Iterable[Diagnostic]) -> tuple[RealizabilityGap, ...]:
    """Project error diagnostics into deduplicated, deterministically ordered gaps.

    Warnings are dropped: a warning is not a reason the backend cannot realize
    the scenario, and surfacing it as a gap would block publication for advice.
    """
    gaps = {
        RealizabilityGap(
            code=diagnostic.code,
            address=diagnostic.address or _PLAN_ADDRESS,
            category=GapCategory.CAPABILITY,
            message=diagnostic.message,
        )
        for diagnostic in diagnostics
        if diagnostic.is_error
    }
    return tuple(sorted(gaps))


def _project_image_demands(provisioning_plan: Any) -> tuple[ImageDemand, ...]:
    """Project each planned node into its bounded image identity.

    Reads the serialized ACES plan payload directly, the same way the
    realization side consumes plan payloads, rather than re-parsing the pack.
    """
    demands = set()
    for resource in getattr(provisioning_plan, "resources", {}).values():
        payload = resource.payload
        if resource.resource_type != _NODE_RESOURCE_TYPE or not isinstance(payload, dict):
            continue
        source = _authored_source(payload)
        demands.add(
            ImageDemand(
                address=resource.address,
                source_name=str(source.get("name") or ""),
                source_version=str(source.get("version") or ""),
                os_family=str(payload.get("os_family") or ""),
            )
        )
    return tuple(sorted(demands))


def _authored_source(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a planned node's authored image source mapping, or an empty one."""
    spec = payload.get("spec")
    node = spec.get("node") if isinstance(spec, Mapping) else None
    source = node.get("source") if isinstance(node, Mapping) else None
    return source if isinstance(source, Mapping) else {}


def _indeterminate(reason: str, exc: BaseException) -> CapabilityAssessment:
    """Build the single-gap INDETERMINATE result for an unassessable scenario.

    Only the failure *class* qualifies the reason, never the exception text.
    ACES load/plan errors embed the absolute scenario path and can echo SDL
    fragments or authored values, and this message reaches the browser -- so
    this follows :func:`shared.aces.sdl_validation.validate_sdl_document`, which
    returns the error class for exactly that reason.
    """
    return CapabilityAssessment(
        outcome=RealizabilityOutcome.INDETERMINATE,
        gaps=(
            RealizabilityGap(
                code=UNASSESSABLE_SCENARIO_CODE,
                address=_PLAN_ADDRESS,
                category=GapCategory.SOURCE_INTEGRITY,
                message=f"{reason} ({type(exc).__name__})",
            ),
        ),
    )
