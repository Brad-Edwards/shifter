"""ECS task dispatch for experiment run script commands.

Serializes resolved ``ScriptCommand`` objects into an ECS task payload and
launches the task via the shared cloud task runner (portal lacks SSM
permissions, so commands run from an ECS Fargate task).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from cms.experiments.ecs import start_experiment_task
from cms.experiments.schemas import RunStatus
from shared.script_context import build_ai_execution_policy_payload

if TYPE_CHECKING:
    from cms.experiments.models import ExperimentRun
    from cms.experiments.orchestrator.execution_plan import ScriptCommand

logger = logging.getLogger("cms.experiments.orchestrator")

_RUN_LOG_FMT = "dispatch_commands: %s (run=%d)"


def dispatch_commands(experiment_id: int, run: ExperimentRun, commands: list[ScriptCommand]) -> None:
    """Dispatch script commands for execution via ECS task.

    Serializes the commands as a JSON payload and starts an ECS Fargate
    task to execute them on the provisioned range instances via SSM.

    On success the ECS task ARN is stored in run.metadata. On failure
    (ECS not configured or API error) the run transitions to FAILED.

    Idempotency: If a task ARN already exists in metadata, logs a warning
    and returns without dispatching to prevent duplicate task submissions
    on retries or duplicate events.

    Args:
        experiment_id: ID of the experiment owning the run.
        run: The ExperimentRun being executed. Must have request_id set.
        commands: List of resolved ScriptCommand objects to execute.
    """
    # Idempotency check: Don't dispatch if already dispatched
    existing_arn = (run.metadata or {}).get("dispatch_task_arn")
    if existing_arn:
        logger.warning(
            "dispatch_commands: run %d already has dispatch_task_arn=%s, skipping duplicate dispatch",
            run.pk,
            existing_arn,
        )
        return

    logger.info(
        "dispatch_commands: dispatching %d commands for run=%d (experiment=%d)",
        len(commands),
        run.pk,
        experiment_id,
    )

    payload = {
        "ai_execution_policy": build_ai_execution_policy_payload(),
        "commands": [asdict(cmd) for cmd in commands],
    }

    if run.request_id is None:
        msg = "ExperimentRun has no request_id — cannot dispatch commands"
        logger.error(_RUN_LOG_FMT, msg, run.pk)
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    dispatch_error: str | None = None
    try:
        task_arn = start_experiment_task(
            experiment_id=experiment_id,
            run_id=run.pk,
            request_id=run.request_id,
            command="execute",
            payload=payload,
        )
    except Exception as exc:
        dispatch_error = f"Failed to start execution ECS task: {exc}"
        logger.exception(_RUN_LOG_FMT, dispatch_error, run.pk)
        task_arn = None

    # Single failure exit covers both the exception and the not-configured
    # (task_arn is None) cases, keeping this function under the return cap (S1142).
    if task_arn is None:
        msg = dispatch_error or "ECS not configured — cannot dispatch experiment commands"
        if dispatch_error is None:
            logger.error(_RUN_LOG_FMT, msg, run.pk)
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    # Store task ARN in metadata for debugging/correlation
    metadata = run.metadata or {}
    metadata["dispatch_task_arn"] = task_arn
    run.metadata = metadata
    run.save(update_fields=["metadata"])

    logger.info(
        "dispatch_commands: started ECS task=%s for run=%d",
        task_arn,
        run.pk,
    )
