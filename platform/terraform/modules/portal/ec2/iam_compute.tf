# Portal EC2 - Compute and Deployment Privileges
#
# Image pull, ECS task execution, Parameter Store deployment config, SSM, and
# ASG lifecycle actions used by user_data.sh.

resource "aws_iam_role_policy" "ecr_pull" {
  name = "ecr-pull"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*" #tfsec:ignore:aws-iam-no-policy-wildcards -- ecr:GetAuthorizationToken requires Resource=* per AWS docs
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = var.ecr_repository_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_run_task" {
  name = "ecs-run-task"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunTask"
        Effect = "Allow"
        Action = [
          "ecs:RunTask"
        ]
        # Allow all revisions of the task definition (CI/CD creates new revisions)
        Resource = "arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task-definition/${var.ecs_task_definition_family}:*"
      },
      {
        Sid    = "ManageTasks"
        Effect = "Allow"
        Action = [
          "ecs:DescribeTasks",
          "ecs:StopTask"
        ]
        Resource = "*" #tfsec:ignore:aws-iam-no-policy-wildcards -- scoped by Condition to specific cluster
        Condition = {
          ArnEquals = {
            "ecs:cluster" = var.ecs_cluster_arn
          }
        }
      },
      {
        Sid    = "PassRole"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          var.ecs_task_role_arn,
          var.ecs_execution_role_arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# IAM policy for reading deployment config from Parameter Store
resource "aws_iam_role_policy" "ssm_parameter_read" {
  count = var.ssm_parameter_store_prefix != "" ? 1 : 0

  name = "ssm-parameter-read"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_parameter_store_prefix}/*"
      }
    ]
  })
}

# IAM policy for ASG lifecycle operations (used by user_data.sh)
resource "aws_iam_role_policy" "lifecycle_action" {
  count = var.enable_autoscaling ? 1 : 0

  name = "lifecycle-action"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "autoscaling:CompleteLifecycleAction"
        ]
        Resource = aws_autoscaling_group.this[0].arn
      },
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingInstances"
        ]
        Resource = "*" #tfsec:ignore:aws-iam-no-policy-wildcards -- Describe* actions require Resource=*
      }
    ]
  })
}
