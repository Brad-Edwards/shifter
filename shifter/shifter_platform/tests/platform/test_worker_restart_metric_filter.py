"""CloudWatch worker restart observability invariants (issue #274).

SQS workers log a structured restart warning when a stale heartbeat file is
present on startup. Terraform must turn that ECS JSON log line into a scoped
custom metric and alarm without conflating it with host remediation (#953) or
portal web capacity (#940).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EC2_MODULE = REPO_ROOT / "platform" / "terraform" / "modules" / "portal" / "ec2"
EC2_VARIABLES_TF = EC2_MODULE / "variables.tf"

RESTART_METRIC = "WorkerRestarts"


def _ec2_module_hcl() -> str:
    """Concatenate every ``*.tf`` in the portal EC2 module.

    Terraform evaluates all sibling files in a directory as one module, so
    these structural invariants are properties of the module rather than of
    any single file. Reading the whole directory keeps them working when the
    module is reorganized across sibling files instead of silently passing on
    a file that no longer holds the resource (#688).
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(EC2_MODULE.glob("*.tf")))


def test_ec2_module_defines_worker_restart_metric_filter() -> None:
    text = _ec2_module_hcl()
    assert "aws_cloudwatch_log_metric_filter" in text
    assert "worker_restarts" in text
    assert "worker_restarts_aggregate" in text
    assert "aws_cloudwatch_log_group.portal.name" in text
    assert "Shifter/Workers/${var.name_prefix}" in text
    assert RESTART_METRIC in text
    assert "WorkerRestartsTotal" in text
    assert "labels.worker_queue" in text
    assert 'Queue = "$.labels.worker_queue"' in text


def test_ec2_module_defines_worker_restart_rate_alarm() -> None:
    text = _ec2_module_hcl()
    assert "worker_restart_rate" in text
    assert "WorkerRestartsTotal" in text
    assert "Shifter/Workers/${var.name_prefix}" in text
    assert "var.worker_health_alarm_actions" in text
    assert 'statistic           = "Sum"' in text


def test_worker_restart_alarm_threshold_variables() -> None:
    variables = EC2_VARIABLES_TF.read_text(encoding="utf-8")
    assert "worker_restart_alarm_threshold" in variables
    assert "worker_restart_alarm_period_seconds" in variables
