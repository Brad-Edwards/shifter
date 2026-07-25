"""CloudWatch worker restart observability invariants (issue #274).

SQS workers log a structured restart warning when a stale heartbeat file is
present on startup. Terraform must turn that ECS JSON log line into a scoped
custom metric and alarm without conflating it with host remediation (#953) or
portal web capacity (#940).
"""

from __future__ import annotations

from pathlib import Path

from _hcl import resource_block

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
    # Log group, namespace, metric name and dimension must belong to the same
    # filter. Asserted independently across the module they would still pass if
    # the per-queue filter watched the wrong log group or emitted into another
    # namespace.
    text = _ec2_module_hcl()
    per_queue = resource_block(text, "aws_cloudwatch_log_metric_filter", "worker_restarts")
    assert "aws_cloudwatch_log_group.portal.name" in per_queue
    assert "Shifter/Workers/${var.name_prefix}" in per_queue
    assert RESTART_METRIC in per_queue
    assert 'Queue = "$.labels.worker_queue"' in per_queue

    aggregate = resource_block(text, "aws_cloudwatch_log_metric_filter", "worker_restarts_aggregate")
    assert "aws_cloudwatch_log_group.portal.name" in aggregate
    assert "Shifter/Workers/${var.name_prefix}" in aggregate
    assert "WorkerRestartsTotal" in aggregate


def test_ec2_module_defines_worker_restart_rate_alarm() -> None:
    # The alarm must watch the aggregate metric in the right namespace, sum it,
    # and route to the configured actions - all as arguments of this one alarm.
    alarm = resource_block(_ec2_module_hcl(), "aws_cloudwatch_metric_alarm", "worker_restart_rate")
    assert "WorkerRestartsTotal" in alarm
    assert "Shifter/Workers/${var.name_prefix}" in alarm
    assert "var.worker_health_alarm_actions" in alarm
    assert '"Sum"' in alarm


def test_worker_restart_alarm_threshold_variables() -> None:
    variables = EC2_VARIABLES_TF.read_text(encoding="utf-8")
    assert "worker_restart_alarm_threshold" in variables
    assert "worker_restart_alarm_period_seconds" in variables
