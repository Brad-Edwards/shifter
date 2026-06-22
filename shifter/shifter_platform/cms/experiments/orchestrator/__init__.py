"""Experiment execution orchestrator package.

Public facade for the experiment run lifecycle. The implementation is split
across cohesive sibling modules (``coordinator``, ``execution_plan``,
``run_provisioning``, ``run_dispatch``, ``run_artifacts``); callers import the
public surface from this package path, which is stable across the split.
"""

from __future__ import annotations

from cms.experiments.orchestrator.coordinator import (
    EVENT_TYPE_EXPERIMENT,
    EVENT_TYPE_RUN,
    ExperimentOrchestrator,
)
from cms.experiments.orchestrator.execution_plan import RunExecutionPlan, ScriptCommand

__all__ = [
    "EVENT_TYPE_EXPERIMENT",
    "EVENT_TYPE_RUN",
    "ExperimentOrchestrator",
    "RunExecutionPlan",
    "ScriptCommand",
]
