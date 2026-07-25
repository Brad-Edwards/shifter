# Portal EC2 - Observability Privileges
#
# CloudWatch Logs writes and the PutMetricData grants for the worker health
# supervisor (#953) and portal capacity publisher (#940).

resource "aws_iam_role_policy" "cloudwatch_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.portal.arn}:*"
      }
    ]
  })
}

# IAM policy for the worker-container health supervisor (#953) to publish
# CloudWatch metrics. cloudwatch:PutMetricData has no resource-level scoping, so
# least privilege is expressed through the cloudwatch:namespace condition,
# constraining it to the Shifter/WorkerHealth namespace.
resource "aws_iam_role_policy" "cloudwatch_metrics" {
  name = "cloudwatch-metrics"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "Shifter/WorkerHealth"
          }
        }
      }
    ]
  })
}

# Portal web capacity metrics (#940). The portal app process publishes
# request/terminal saturation gauges to the Shifter/PortalCapacity namespace.
# This is a SEPARATE least-privilege statement, constrained by its own
# cloudwatch:namespace condition, rather than widening the worker-health policy
# above — keeping web-capacity emission and worker-container liveness on distinct
# grants. cloudwatch:PutMetricData has no resource-level scoping, so the
# namespace condition is the boundary.
resource "aws_iam_role_policy" "cloudwatch_metrics_portal_capacity" {
  name = "cloudwatch-metrics-portal-capacity"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "Shifter/PortalCapacity"
          }
        }
      }
    ]
  })
}
