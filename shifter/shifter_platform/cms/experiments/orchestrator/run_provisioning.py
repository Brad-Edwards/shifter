"""Range provisioning for experiment runs.

Connects the experiment system to the engine's range provisioning pipeline,
following the same hydrate → RequestSpec → engine pattern as
``cms.services.create_range``, adapted for experiment runs.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from cms.exceptions import CMSError
from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.schemas import RunStatus
from cms.models import AgentConfig, RangeInstance, Request
from cms.scenarios.hydrator import hydrate_scenario
from engine.services import create_range as engine_create_range
from shared.enums import RequestType
from shared.schemas import RequestSpec

logger = logging.getLogger("cms.experiments.orchestrator")


def request_range_provisioning(experiment: Experiment, run: ExperimentRun) -> None:
    """Request range provisioning for a run via the engine.

    Follows the same hydrate → RequestSpec → engine pattern as
    cms.services.create_range, adapted for experiment runs:
    - No "active range" guard (experiments provision many ranges)
    - Agent comes from experiment.agent rather than per-request input
    - request_id is stored on ExperimentRun for event correlation

    On failure the run is transitioned to FAILED with an error message
    and the function returns (does not raise).

    Args:
        experiment: The Experiment owning the run.
        run: The ExperimentRun to provision a range for. Must already be
            in PROVISIONING status.
    """
    scenario_id: str = experiment.scenario_id
    user = experiment.user

    logger.info(
        "request_range_provisioning: run=%d experiment=%d scenario=%s user=%d",
        run.pk,
        experiment.pk,
        scenario_id,
        user.pk,
    )

    # --- Build agents dict from experiment's agent ---
    agent: AgentConfig | None = experiment.agent
    agents: dict[str, AgentConfig] = {}

    if agent is not None:
        if agent.deleted_at is not None:
            msg = f"Agent '{agent.name}' (id={agent.pk}) has been deleted"
            logger.error(
                "request_range_provisioning: %s (run=%d)",
                msg,
                run.pk,
            )
            run.error_message = msg
            run.save(update_fields=["error_message"])
            run.transition_to(RunStatus.FAILED)
            return

        os_key = "windows" if agent.os.slug.lower() == "windows" else "linux"
        agents[os_key] = agent

    # --- Hydrate scenario ---
    try:
        range_spec = hydrate_scenario(scenario_id, user.pk, agents)
    except (CMSError, ValueError) as exc:
        msg = f"Scenario hydration failed for '{scenario_id}': {exc}"
        logger.error(
            "request_range_provisioning: %s (run=%d)",
            msg,
            run.pk,
        )
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    # --- Create CMS Request record ---
    request_id = uuid4()
    cms_request = Request.objects.create(
        request_id=request_id,
        request_type=RequestType.RANGE.value,
        user=user,
    )

    # --- Store request_id on run for event correlation ---
    run.request_id = request_id
    run.save(update_fields=["request_id"])

    logger.info(
        "request_range_provisioning: created Request %s for run=%d",
        request_id,
        run.pk,
    )

    # --- Wrap RangeSpec in RequestSpec and call engine ---
    request_spec = RequestSpec(
        request_id=request_id,
        user_id=user.pk,
        items=[range_spec],
    )

    try:
        engine_create_range(request_spec)
    except Exception as exc:
        msg = f"Engine create_range failed: {exc}"
        logger.exception(
            "request_range_provisioning: %s (run=%d, request_id=%s)",
            msg,
            run.pk,
            request_id,
        )
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    # --- Create RangeInstance tracking record ---
    RangeInstance.objects.create(
        request=cms_request,
        scenario_id=scenario_id,
        user_id=user.pk,
        agent=agent,
        range_spec=range_spec.model_dump(mode="json"),
    )

    logger.info(
        "request_range_provisioning: provisioning triggered for run=%d request_id=%s scenario=%s",
        run.pk,
        request_id,
        scenario_id,
    )
