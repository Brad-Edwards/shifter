"""Experiment execution orchestrator coordinator.

Manages the lifecycle of experiment runs:
1. Schedule pending runs up to max_parallel limit
2. Handle range provisioning completion
3. Execute victim scripts, then attacker scripts
4. Collect artifacts
5. Determine overall experiment outcome

Actual SSM commands are dispatched via ECS tasks (portal lacks SSM permissions).
The lifecycle phases live in sibling modules (``execution_plan``,
``run_provisioning``, ``run_dispatch``, ``run_artifacts``); this coordinator
keeps the public entry points and overall scheduling/completion logic.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from cms.experiments.models import Experiment, ExperimentRun
from cms.experiments.orchestrator import (
    execution_plan,
    run_artifacts,
    run_dispatch,
    run_provisioning,
)
from cms.experiments.schemas import (
    TERMINAL_RUN_STATUSES,
    ExperimentStatus,
    RunStatus,
)
from risk_register.models import AuditLog
from risk_register.services import StateChange, audit_log_system_event

logger = logging.getLogger("cms.experiments.orchestrator")


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

EVENT_TYPE_EXPERIMENT = "experiment.status.updated"
EVENT_TYPE_RUN = "experiment.run.updated"


class ExperimentOrchestrator:
    """Coordinates experiment run execution.

    Follows SetupOrchestrator pattern but manages the full experiment lifecycle.
    """

    def __init__(self, experiment_id: int) -> None:
        self.experiment_id = experiment_id
        self._experiment: Experiment | None = None

    @property
    def experiment(self) -> Experiment:
        if self._experiment is None:
            self._experiment = Experiment.objects.prefetch_related("scripts__script", "runs").get(pk=self.experiment_id)
        return self._experiment

    def refresh(self) -> None:
        """Reload experiment from database."""
        self._experiment = None

    # -----------------------------------------------------------------
    # Main entry points (called by SQS handler)
    # -----------------------------------------------------------------

    def schedule_runs(self) -> int:
        """Schedule pending runs up to max_parallel limit.

        Uses select_for_update() to prevent concurrent SQS handlers from
        over-scheduling runs beyond max_parallel_runs.

        Returns:
            Number of runs scheduled for provisioning.
        """
        with transaction.atomic():
            experiment = Experiment.objects.select_for_update().get(pk=self.experiment_id)
            self._experiment = experiment

            if experiment.status == ExperimentStatus.QUEUED.value:
                experiment.transition_to(ExperimentStatus.RUNNING)
                experiment = Experiment.objects.select_for_update().get(pk=self.experiment_id)
                self._experiment = experiment

            if experiment.status != ExperimentStatus.RUNNING.value:
                logger.info(
                    "schedule_runs: experiment %s not running (status=%s), skipping",
                    self.experiment_id,
                    experiment.status,
                )
                return 0

            active_runs = ExperimentRun.objects.filter(
                experiment=experiment,
                status__in=[
                    RunStatus.PROVISIONING.value,
                    RunStatus.EXECUTING_VICTIMS.value,
                    RunStatus.EXECUTING_ATTACKER.value,
                    RunStatus.COLLECTING.value,
                ],
            ).count()

            slots_available = experiment.max_parallel_runs - active_runs
            if slots_available <= 0:
                logger.debug(
                    "schedule_runs: no slots (active=%d, max=%d)",
                    active_runs,
                    experiment.max_parallel_runs,
                )
                return 0

            pending_runs = list(
                ExperimentRun.objects.select_for_update()
                .filter(
                    experiment=experiment,
                    status=RunStatus.PENDING.value,
                )
                .order_by("run_number")[:slots_available]
            )

            scheduled = 0
            for run in pending_runs:
                try:
                    run.transition_to(RunStatus.PROVISIONING)
                    run_provisioning.request_range_provisioning(experiment, run)
                    scheduled += 1
                except Exception:
                    logger.exception(
                        "schedule_runs: failed to schedule run %s (experiment=%s)",
                        run.pk,
                        self.experiment_id,
                    )
                    run.error_message = "Failed to schedule provisioning"
                    run.save(update_fields=["error_message"])
                    run.transition_to(RunStatus.FAILED)

        logger.info(
            "schedule_runs: scheduled %d runs for experiment %s",
            scheduled,
            self.experiment_id,
        )
        return scheduled

    def handle_range_provisioned(self, run_id: int, provisioned_instances: dict[str, Any]) -> None:
        """Handle range provisioning completion.

        Args:
            run_id: ID of the ExperimentRun.
            provisioned_instances: Dict of instance names to their details.
        """
        logger.debug("handle_range_provisioned called for run %s", run_id)
        if not isinstance(provisioned_instances, dict):
            logger.error(
                "handle_range_provisioned: provisioned_instances is not a dict (type=%s) for run %s",
                type(provisioned_instances).__name__,
                run_id,
            )
            provisioned_instances = {}

        try:
            run = ExperimentRun.objects.get(pk=run_id, experiment_id=self.experiment_id)
        except ExperimentRun.DoesNotExist:
            logger.warning("handle_range_provisioned: run %s not found", run_id)
            return

        run.metadata = {"provisioned_instances": provisioned_instances}
        run.save(update_fields=["metadata"])

        try:
            plan = execution_plan.build_execution_plan(self.experiment_id, run, provisioned_instances)
        except Exception:
            logger.exception("handle_range_provisioned: plan build failed for run %s", run_id)
            run.error_message = "Failed to build execution plan"
            run.save(update_fields=["error_message"])
            run.transition_to(RunStatus.FAILED)
            self._check_experiment_completion()
            return

        if plan.victim_commands:
            run.transition_to(RunStatus.EXECUTING_VICTIMS)
            run_dispatch.dispatch_commands(self.experiment_id, run, plan.victim_commands)
        elif plan.attacker_commands:
            run.transition_to(RunStatus.EXECUTING_VICTIMS)
            run.transition_to(RunStatus.EXECUTING_ATTACKER)
            run_dispatch.dispatch_commands(self.experiment_id, run, plan.attacker_commands)
        else:
            run.transition_to(RunStatus.EXECUTING_VICTIMS)
            run.transition_to(RunStatus.EXECUTING_ATTACKER)
            run.transition_to(RunStatus.COLLECTING)
            run.transition_to(RunStatus.COMPLETED)
            self._check_experiment_completion()

    def handle_victim_scripts_completed(self, run_id: int) -> None:
        """Handle victim script completion — start attacker scripts."""
        logger.debug("handle_victim_scripts_completed called for run %s", run_id)
        try:
            try:
                run = ExperimentRun.objects.get(pk=run_id, experiment_id=self.experiment_id)
            except ExperimentRun.DoesNotExist:
                logger.warning("handle_victim_scripts_completed: run %s not found", run_id)
                return

            provisioned_instances = (run.metadata or {}).get("provisioned_instances", {})

            try:
                plan = execution_plan.build_execution_plan(self.experiment_id, run, provisioned_instances)
            except Exception:
                logger.exception("handle_victim_scripts_completed: plan failed for run %s", run_id)
                run.error_message = "Failed to build attacker execution plan"
                run.save(update_fields=["error_message"])
                run.transition_to(RunStatus.FAILED)
                self._check_experiment_completion()
                return

            if plan.attacker_commands:
                run.transition_to(RunStatus.EXECUTING_ATTACKER)
                run_dispatch.dispatch_commands(self.experiment_id, run, plan.attacker_commands)
            else:
                run.transition_to(RunStatus.EXECUTING_ATTACKER)
                run.transition_to(RunStatus.COLLECTING)
                run.transition_to(RunStatus.COMPLETED)
                self._check_experiment_completion()
        except Exception:
            logger.exception("handle_victim_scripts_completed: unexpected error for run %s", run_id)
            self.handle_run_failed(run_id, "Unexpected orchestrator error during victim completion")

    def handle_attacker_scripts_completed(self, run_id: int) -> None:
        """Handle attacker script completion — collect artifacts."""
        logger.debug("handle_attacker_scripts_completed called for run %s", run_id)
        try:
            try:
                run = ExperimentRun.objects.get(pk=run_id, experiment_id=self.experiment_id)
            except ExperimentRun.DoesNotExist:
                logger.warning("handle_attacker_scripts_completed: run %s not found", run_id)
                return

            run.transition_to(RunStatus.COLLECTING)
            run_artifacts.collect_artifacts(self.experiment_id, run)
        except Exception:
            logger.exception("handle_attacker_scripts_completed: unexpected error for run %s", run_id)
            self.handle_run_failed(run_id, "Unexpected orchestrator error during attacker completion")

    def handle_artifacts_collected(self, run_id: int) -> None:
        """Handle artifact collection completion — mark run complete."""
        logger.debug("handle_artifacts_collected called for run %s", run_id)
        try:
            try:
                run = ExperimentRun.objects.get(pk=run_id, experiment_id=self.experiment_id)
            except ExperimentRun.DoesNotExist:
                logger.warning("handle_artifacts_collected: run %s not found", run_id)
                return

            run.transition_to(RunStatus.COMPLETED)
            logger.info("handle_artifacts_collected: run %s completed", run_id)
            self.schedule_runs()
            self._check_experiment_completion()
        except Exception:
            logger.exception("handle_artifacts_collected: unexpected error for run %s", run_id)
            self.handle_run_failed(run_id, "Unexpected orchestrator error during artifact collection")

    def handle_run_failed(self, run_id: int, error_message: str = "") -> None:
        """Handle run failure."""
        logger.debug("handle_run_failed called for run %s error=%s", run_id, error_message)
        try:
            try:
                run = ExperimentRun.objects.get(pk=run_id, experiment_id=self.experiment_id)
            except ExperimentRun.DoesNotExist:
                logger.warning("handle_run_failed: run %s not found", run_id)
                return

            if run.status in {s.value for s in TERMINAL_RUN_STATUSES}:
                return

            run.error_message = error_message
            run.save(update_fields=["error_message"])
            run.transition_to(RunStatus.FAILED)

            logger.warning("handle_run_failed: run %s failed: %s", run_id, error_message)
            self.schedule_runs()
            self._check_experiment_completion()
        except Exception:
            logger.exception("handle_run_failed: unexpected error for run %s", run_id)

    # -----------------------------------------------------------------
    # Completion / audit
    # -----------------------------------------------------------------

    def _check_experiment_completion(self) -> None:
        """Check if all runs are terminal and update experiment status."""
        try:
            self.refresh()
            experiment = self.experiment

            if experiment.status != ExperimentStatus.RUNNING.value:
                return

            all_runs = ExperimentRun.objects.filter(experiment=experiment)
            total = all_runs.count()
            if total == 0:
                return

            terminal_count = all_runs.filter(status__in=[s.value for s in TERMINAL_RUN_STATUSES]).count()

            if terminal_count < total:
                return

            completed_count = all_runs.filter(status=RunStatus.COMPLETED.value).count()
            failed_count = all_runs.filter(status=RunStatus.FAILED.value).count()

            if failed_count == total:
                experiment.error_message = f"All {failed_count} runs failed"
                experiment.save(update_fields=["error_message"])
                experiment.transition_to(ExperimentStatus.FAILED)
                audit_log_system_event(
                    entity_type=AuditLog.EntityType.EXPERIMENT,
                    entity_id=self.experiment_id,
                    action=AuditLog.Action.FAILED,
                    source="experiments.orchestrator",
                    context=experiment.error_message or "",
                )
            else:
                experiment.transition_to(ExperimentStatus.COMPLETED)
                audit_log_system_event(
                    entity_type=AuditLog.EntityType.EXPERIMENT,
                    entity_id=self.experiment_id,
                    action=AuditLog.Action.READY,
                    source="experiments.orchestrator",
                    state=StateChange(new={"completed_runs": completed_count, "failed_runs": failed_count}),
                )

            logger.info(
                "_check_experiment_completion: experiment %s finished (%d/%d succeeded)",
                self.experiment_id,
                completed_count,
                total,
            )
        except Exception:
            logger.exception("_check_experiment_completion: unexpected error for experiment %s", self.experiment_id)
