"""Artifact collection for experiment runs.

Triggers an ECS Fargate task that copies output files from range instances to
S3 and creates RunArtifact/ExperimentArtifact records.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cms.experiments.ecs import start_experiment_task
from cms.experiments.schemas import RunStatus

if TYPE_CHECKING:
    from cms.experiments.models import ExperimentRun

logger = logging.getLogger("cms.experiments.orchestrator")

_RUN_LOG_FMT = "collect_artifacts: %s (run=%d)"


def collect_artifacts(experiment_id: int, run: ExperimentRun) -> None:
    """Trigger artifact collection from range instances via ECS task.

    Starts an ECS Fargate task that copies output files from range
    instances to S3 and creates RunArtifact/ExperimentArtifact records.

    On success the ECS task ARN is stored in run.metadata. On failure
    the run transitions to FAILED.

    Idempotency: If a collection task ARN already exists in metadata, logs
    a warning and returns without dispatching to prevent duplicate task
    submissions on retries or duplicate events.

    Args:
        experiment_id: ID of the experiment owning the run.
        run: The ExperimentRun to collect artifacts for.
            Must have request_id set.
    """
    # Idempotency check: Don't collect if already started
    existing_arn = (run.metadata or {}).get("collect_task_arn")
    if existing_arn:
        logger.warning(
            "collect_artifacts: run %d already has collect_task_arn=%s, skipping duplicate collection",
            run.pk,
            existing_arn,
        )
        return

    logger.info(
        "collect_artifacts: collecting for run=%d (experiment=%d)",
        run.pk,
        experiment_id,
    )

    if run.request_id is None:
        msg = "ExperimentRun has no request_id — cannot collect artifacts"
        logger.error(_RUN_LOG_FMT, msg, run.pk)
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    collect_error: str | None = None
    try:
        task_arn = start_experiment_task(
            experiment_id=experiment_id,
            run_id=run.pk,
            request_id=run.request_id,
            command="collect",
        )
    except Exception as exc:
        collect_error = f"Failed to start collection ECS task: {exc}"
        logger.exception(_RUN_LOG_FMT, collect_error, run.pk)
        task_arn = None

    # Single failure exit covers both the exception and the not-configured
    # (task_arn is None) cases, keeping this function under the return cap (S1142).
    if task_arn is None:
        msg = collect_error or "ECS not configured — cannot collect experiment artifacts"
        if collect_error is None:
            logger.error(_RUN_LOG_FMT, msg, run.pk)
        run.error_message = msg
        run.save(update_fields=["error_message"])
        run.transition_to(RunStatus.FAILED)
        return

    # Store task ARN in metadata for debugging/correlation
    metadata = run.metadata or {}
    metadata["collect_task_arn"] = task_arn
    run.metadata = metadata
    run.save(update_fields=["metadata"])

    logger.info(
        "collect_artifacts: started ECS task=%s for run=%d",
        task_arn,
        run.pk,
    )
