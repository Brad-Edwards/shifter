"""Experiment run execution-plan construction.

Builds the ordered victim/attacker command set for a run from its experiment's
script assignments and the provisioned-instance data. Every user-controlled
script/prompt/runtime value is rendered through a validated
``shared.script_context.ScriptExecutionContext``; bad inputs surface as
``ExecutionPlanError`` rather than raw ``ValidationError`` or unsafe shell text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from pydantic import ValidationError

from cms.experiments.exceptions import ExecutionPlanError
from cms.experiments.models import ExperimentRun, ExperimentScript
from cms.experiments.schemas import ScriptType
from shared.script_context import ScriptExecutionContext
from shared.template_vars import build_instance_data

logger = logging.getLogger("cms.experiments.orchestrator")


@dataclass
class ScriptCommand:
    """A resolved script command ready for execution on an instance."""

    instance_name: str
    instance_id: str
    script_type: str
    command: str
    execution_order: int
    script_s3_key: str | None = None


@dataclass
class RunExecutionPlan:
    """Plan for executing a single experiment run."""

    run_id: int
    victim_commands: list[ScriptCommand] = field(default_factory=list)
    attacker_commands: list[ScriptCommand] = field(default_factory=list)


def build_execution_plan(
    experiment_id: int,
    run: ExperimentRun,
    provisioned_instances: dict[str, Any],
) -> RunExecutionPlan:
    """Build an execution plan from experiment scripts and provisioned data.

    Raises:
        ExecutionPlanError: If required instances are missing from provisioned data,
            or if the configured cloud provider is not AWS (experiment script
            execution lands in SSM RunCommand, which is AWS-only today).
    """
    _enforce_aws_only_provider(run)

    instance_data = build_instance_data(provisioned_instances)
    scripts = (
        ExperimentScript.objects.filter(
            experiment_id=experiment_id,
        )
        .select_related("script")
        .order_by("execution_order")
    )

    plan = RunExecutionPlan(run_id=run.pk)
    missing_instances: list[str] = []

    for script_assignment in scripts:
        instance_name = script_assignment.instance_name
        instance_info = provisioned_instances.get(instance_name, {})
        instance_id = instance_info.get("instance_id", "")

        if not instance_id:
            logger.warning(
                "build_execution_plan: no instance_id for %s in run %s",
                instance_name,
                run.pk,
            )
            missing_instances.append(instance_name)
            continue

        cmd = _build_script_command(
            run=run,
            script_assignment=script_assignment,
            instance_name=instance_name,
            instance_info=instance_info,
            instance_id=instance_id,
            instance_data=instance_data,
        )

        if script_assignment.execution_order < 100:
            plan.victim_commands.append(cmd)
        else:
            plan.attacker_commands.append(cmd)

    # Fail fast if instances are missing
    if missing_instances:
        raise ExecutionPlanError(f"Cannot build execution plan for run {run.pk}: missing instances {missing_instances}")

    return plan


def _enforce_aws_only_provider(run: ExperimentRun) -> None:
    """Gate non-AWS providers with a clear ``ExecutionPlanError`` before plan construction.

    ``cyberscript.script_context.ScriptExecutionContext`` validates EC2 instance
    IDs and renders ``aws s3 cp`` shell text; today's dispatch path lands in SSM
    RunCommand, which is AWS-only. Surface this explicitly rather than letting
    the validator's ``i-…`` rejection masquerade as an unsupported-provider error.
    """
    provider = (getattr(settings, "CLOUD_PROVIDER", None) or "aws").lower()
    if provider != "aws":
        raise ExecutionPlanError(
            f"Cannot build execution plan for run {run.pk}: experiment "
            f"script execution is AWS-only today (CLOUD_PROVIDER={provider!r})."
        )


def _build_script_command(
    *,
    run: ExperimentRun,
    script_assignment: ExperimentScript,
    instance_name: str,
    instance_info: dict[str, Any],
    instance_id: str,
    instance_data: dict[str, dict[str, Any]],
) -> ScriptCommand:
    """Construct a single ``ScriptCommand`` from a script assignment.

    Wraps ``ScriptExecutionContext`` construction and rendering so the loop in
    ``build_execution_plan`` stays declarative. Any ``pydantic.ValidationError``
    is surfaced as ``ExecutionPlanError`` with the rejected input value redacted
    from the message — Pydantic's default ``str()`` includes ``input_value=``.
    """
    private_ip = instance_info.get("private_ip") or None

    try:
        if script_assignment.script_type == ScriptType.PYTHON.value:
            return _build_python_script_command(
                script_assignment=script_assignment,
                instance_name=instance_name,
                instance_id=instance_id,
                private_ip=private_ip,
            )
        if script_assignment.script_type == ScriptType.CLAUDE_CODE.value:
            return _build_claude_script_command(
                script_assignment=script_assignment,
                instance_name=instance_name,
                instance_id=instance_id,
                private_ip=private_ip,
                instance_data=instance_data,
            )
        raise ExecutionPlanError(
            f"Cannot build execution plan for run {run.pk}: unknown script_type for instance '{instance_name}'"
        )
    except ValidationError as exc:
        summary = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors(include_input=False)
        )
        raise ExecutionPlanError(
            f"Cannot build execution plan for run {run.pk}: script for instance failed validation: {summary}"
        ) from exc


def _build_python_script_command(
    *,
    script_assignment: ExperimentScript,
    instance_name: str,
    instance_id: str,
    private_ip: str | None,
) -> ScriptCommand:
    """Render a Python-script command via a validated ``ScriptExecutionContext``."""
    s3_key = script_assignment.script.s3_key if script_assignment.script else ""
    ctx = ScriptExecutionContext.for_python(
        instance_name=instance_name,
        instance_id=instance_id,
        private_ip=private_ip,
        script_s3_key=s3_key,
    )
    return ScriptCommand(
        instance_name=instance_name,
        instance_id=instance_id,
        script_type=ScriptType.PYTHON.value,
        command=ctx.render_command(),
        execution_order=script_assignment.execution_order,
        script_s3_key=s3_key,
    )


def _build_claude_script_command(
    *,
    script_assignment: ExperimentScript,
    instance_name: str,
    instance_id: str,
    private_ip: str | None,
    instance_data: dict[str, dict[str, Any]],
) -> ScriptCommand:
    """Render a Claude-prompt command via a validated ``ScriptExecutionContext``."""
    ctx = ScriptExecutionContext.for_claude(
        instance_name=instance_name,
        instance_id=instance_id,
        private_ip=private_ip,
        claude_prompt_template=script_assignment.claude_prompt,
        instance_data=instance_data,
    )
    return ScriptCommand(
        instance_name=instance_name,
        instance_id=instance_id,
        script_type=ScriptType.CLAUDE_CODE.value,
        command=ctx.render_command(),
        execution_order=script_assignment.execution_order,
    )
