# Portal EC2 - Container Logging and Log-Derived Alarms
#
# CloudWatch log group for the portal container plus the log-derived worker
# restart signal (#274/#953). Portal capacity alarms live in observability.tf.

# ------------------------------------------------------------------------------
# CloudWatch Log Group for Portal Container
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "portal" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.cloudwatch_logs.arn

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-portal-logs"
  })
}

# Alarm on the aggregate UnhealthyWorkers metric emitted by the worker-container
# health supervisor (#953). The host agent restarts unhealthy workers and emits
# this metric every health interval; the alarm makes a persistently-unhealthy
# worker (one that keeps failing its restart) visible to operators. Shape mirrors
# the portal redis / messaging alarm convention; actions are wired from the
# per-environment SNS alerts topic via var.worker_health_alarm_actions.
resource "aws_cloudwatch_metric_alarm" "unhealthy_workers" {
  alarm_name          = "${var.name_prefix}-unhealthy-workers"
  alarm_description   = "One or more Shifter worker/scheduler containers are unhealthy and not recovering on ${var.name_prefix}"
  namespace           = "Shifter/WorkerHealth"
  metric_name         = "UnhealthyWorkers"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 2
  period              = 60
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  # Scope the alarm to this environment's metric series; the supervisor emits the
  # matching NamePrefix dimension. CloudWatch metrics are account/region scoped,
  # so without this dev and prod would share one series and cross-trip.
  dimensions = {
    NamePrefix = var.name_prefix
  }

  alarm_actions = var.worker_health_alarm_actions
  ok_actions    = var.worker_health_alarm_actions

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-unhealthy-workers"
  })
}

# Log-derived SQS worker restart signal (#274). Distinct from the #953 host
# supervisor's Shifter/WorkerHealth metrics and from Shifter/PortalCapacity.
# Per-queue series for diagnostics; a separate aggregate series feeds the alarm
# because CloudWatch alarms require a concrete metric, not SEARCH().
resource "aws_cloudwatch_log_metric_filter" "worker_restarts" {
  name           = "${var.name_prefix}-worker-restarts"
  log_group_name = aws_cloudwatch_log_group.portal.name
  pattern        = "{ ($.message = \"*Worker restart detected*\") && ($.labels.worker_queue = \"*\") }"

  metric_transformation {
    name      = "WorkerRestarts"
    namespace = "Shifter/Workers/${var.name_prefix}"
    value     = "1"

    dimensions = {
      Queue = "$.labels.worker_queue"
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "worker_restarts_aggregate" {
  name           = "${var.name_prefix}-worker-restarts-aggregate"
  log_group_name = aws_cloudwatch_log_group.portal.name
  pattern        = "{ ($.message = \"*Worker restart detected*\") && ($.labels.worker_queue = \"*\") }"

  metric_transformation {
    name          = "WorkerRestartsTotal"
    namespace     = "Shifter/Workers/${var.name_prefix}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_restart_rate" {
  alarm_name          = "${var.name_prefix}-worker-restart-rate"
  alarm_description   = "SQS workers restarting frequently on ${var.name_prefix} (#274)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "WorkerRestartsTotal"
  namespace           = "Shifter/Workers/${var.name_prefix}"
  period              = var.worker_restart_alarm_period_seconds
  statistic           = "Sum"
  threshold           = var.worker_restart_alarm_threshold
  treat_missing_data  = "notBreaching"

  alarm_actions = var.worker_health_alarm_actions
  ok_actions    = var.worker_health_alarm_actions

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-worker-restart-rate"
  })
}
