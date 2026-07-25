# Portal EC2 - Auto Scaling Group, Scaling Policy, and Lifecycle Hooks
#
# ALB target-tracking policies live in autoscaling_alb.tf; capacity alarms in
# observability.tf.

# ------------------------------------------------------------------------------
# Auto Scaling Group (for ASG mode)
# ------------------------------------------------------------------------------

resource "aws_autoscaling_group" "this" {
  count = var.enable_autoscaling ? 1 : 0

  name_prefix               = "${var.name_prefix}-asg-"
  vpc_zone_identifier       = var.subnet_ids
  target_group_arns         = [var.target_group_arn]
  health_check_type         = var.health_check_type
  health_check_grace_period = var.health_check_grace_period

  # Do not block `terraform apply` on the ASG reaching capacity. New instances
  # sit in Pending:Wait until user_data finishes and calls
  # CompleteLifecycleAction (docker install + portal container boot, minutes
  # each), and warm-pool churn compounds it, so a multi-instance scale-up (e.g.
  # 2 -> 6 on a larger instance type) blows past Terraform's default 10m
  # capacity wait and fails the apply mid-roll (#1462). Readiness is gated
  # downstream by the instance refresh below plus the deploy's verify-digest /
  # verify-workers / health steps, so the in-apply wait is redundant.
  wait_for_capacity_timeout = "0"

  min_size         = var.asg_min_size
  max_size         = var.asg_max_size
  desired_capacity = var.asg_desired_capacity

  launch_template {
    id      = aws_launch_template.this[0].id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = var.instance_refresh_min_healthy_percentage
      instance_warmup        = var.instance_refresh_instance_warmup
    }
  }

  dynamic "warm_pool" {
    for_each = var.asg_warm_pool_min_size > 0 ? [1] : []

    content {
      min_size   = var.asg_warm_pool_min_size
      pool_state = var.asg_warm_pool_state

      instance_reuse_policy {
        reuse_on_scale_in = true
      }
    }
  }

  dynamic "tag" {
    for_each = merge(local.common_tags, {
      Name = "${var.name_prefix}-ec2"
    })
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ------------------------------------------------------------------------------
# Auto Scaling Policies
# ------------------------------------------------------------------------------
# Portal autoscaling is driven by request-path saturation, not average EC2 CPU
# (#940). The primary scale-out/scale-in policies are ALB target-tracking
# (ALBRequestCountPerTarget + TargetResponseTime) in autoscaling_alb.tf; the
# simple policy below is an *additive* app-saturation scale-out, triggered by
# the Shifter/PortalCapacity WorkerBusyRatio alarm in observability.tf. There is
# deliberately NO CPU-low / simple scale-IN policy: leaving CPU-low as a scale-in
# path alongside target tracking lets a latency-saturated-but-low-CPU fleet scale
# in (the documented #851 / #940 failure mode), so target tracking owns the
# saturation-aware, drain-respecting scale-in. Average EC2 CPU remains only as a
# guardrail *notification* alarm (observability.tf), not a scaling action.

resource "aws_autoscaling_policy" "scale_up" {
  count = var.enable_autoscaling ? 1 : 0

  name                   = "${var.name_prefix}-app-saturation-scale-out"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = var.scale_out_cooldown_seconds
  autoscaling_group_name = aws_autoscaling_group.this[0].name
}

# ------------------------------------------------------------------------------
# ASG Lifecycle Hook (holds instance until user_data completes deployment)
# ------------------------------------------------------------------------------

resource "aws_autoscaling_lifecycle_hook" "launch" {
  count = var.enable_autoscaling ? 1 : 0

  name                   = "${var.name_prefix}-launch-hook"
  autoscaling_group_name = aws_autoscaling_group.this[0].name
  lifecycle_transition   = "autoscaling:EC2_INSTANCE_LAUNCHING"
  heartbeat_timeout      = var.lifecycle_hook_heartbeat_timeout
  default_result         = "ABANDON"
}

# ------------------------------------------------------------------------------
# ASG Termination Drain Hook (bounded drain for long-lived connections)
# ------------------------------------------------------------------------------
# Holds a terminating instance in Terminating:Wait for a bounded window so that,
# during an instance refresh or scale-in, the ALB has time to deregister the
# target (target-group deregistration_delay) and existing terminal / RDP / SSH
# WebSocket sessions can drain before the container is SIGKILLed (issue #931,
# DP-21). This is a passive timeout-only drain: no instance-side
# CompleteLifecycleAction is required, and default_result = "CONTINUE" lets the
# termination proceed automatically once heartbeat_timeout elapses, so no
# instance ever gets stuck. Kept separate from the launch hook above so launch
# bootstrap success never depends on termination-drain logic. The instance IAM
# role already scopes autoscaling:CompleteLifecycleAction to this ASG, so an
# early-completion path can be added later without an IAM change.
resource "aws_autoscaling_lifecycle_hook" "terminate" {
  count = var.enable_autoscaling ? 1 : 0

  name                   = "${var.name_prefix}-terminate-hook"
  autoscaling_group_name = aws_autoscaling_group.this[0].name
  lifecycle_transition   = "autoscaling:EC2_INSTANCE_TERMINATING"
  heartbeat_timeout      = var.termination_drain_timeout
  default_result         = "CONTINUE"
}
