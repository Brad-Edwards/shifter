# GitHub OIDC - Compute Category Policy (#254)

# Compute: EC2, Auto Scaling, Lambda, ECS
# checkov:skip=CKV_AWS_355:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "compute" {
  # checkov:skip=CKV_AWS_287:CI/CD requires broad compute permissions for infrastructure management. Risk accepted, see #44
  name = "shifter-${var.environment}-compute"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # TODO: Scope down EC2 permissions - see GitHub issue for audit
      {
        Sid      = "EC2"
        Effect   = "Allow"
        Action   = ["ec2:*"]
        Resource = "*"
      },
      {
        # Packer's amazon-ebs SSM communicator (ssh_interface = "session_manager",
        # used by the no-inbound techvault / polaris-vm scenario bakes) opens an
        # SSH-over-SSM tunnel to the EC2 builder via the AWS-StartSSHSession
        # document. The management policy's SSMRunCommand grant covers SendCommand
        # but not StartSession, so the scenario bakes fail with AccessDenied
        # without this. Lives in the compute policy (it targets EC2 build hosts)
        # to keep the management managed-policy under the 6144-char limit (#254).
        Sid    = "SSMSessionManagerForPackerBuilds"
        Effect = "Allow"
        Action = [
          "ssm:StartSession",
          "ssm:TerminateSession",
          "ssm:ResumeSession"
        ]
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*",
          "arn:aws:ssm:${var.aws_region}::document/AWS-StartSSHSession",
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:session/*"
        ]
      },
      {
        Sid    = "AutoScaling"
        Effect = "Allow"
        Action = [
          "autoscaling:CreateAutoScalingGroup",
          "autoscaling:DeleteAutoScalingGroup",
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:UpdateAutoScalingGroup",
          "autoscaling:CreateLaunchConfiguration",
          "autoscaling:DeleteLaunchConfiguration",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:CreateOrUpdateTags",
          "autoscaling:DeleteTags",
          "autoscaling:DescribeTags",
          "autoscaling:PutScalingPolicy",
          "autoscaling:DeletePolicy",
          "autoscaling:DescribePolicies",
          "autoscaling:SetDesiredCapacity",
          "autoscaling:TerminateInstanceInAutoScalingGroup",
          "autoscaling:StartInstanceRefresh",
          "autoscaling:DescribeInstanceRefreshes",
          "autoscaling:DescribeScalingActivities",
          # Lifecycle hooks (launch + termination-drain) managed by
          # modules/portal/ec2. Describe is needed at plan/refresh time,
          # Put/Delete at apply time (including destroying hooks left in
          # state when enable_autoscaling is toggled off, as in dev).
          "autoscaling:DescribeLifecycleHooks",
          "autoscaling:PutLifecycleHook",
          "autoscaling:DeleteLifecycleHook",
          # Warm pool, managed by the same module's dynamic "warm_pool"
          # block when asg_warm_pool_min_size > 0.
          "autoscaling:DescribeWarmPool",
          "autoscaling:PutWarmPool",
          "autoscaling:DeleteWarmPool"
        ]
        Resource = "*"
      },
      {
        Sid    = "ApplicationAutoScaling"
        Effect = "Allow"
        Action = [
          "application-autoscaling:RegisterScalableTarget",
          "application-autoscaling:DeregisterScalableTarget",
          "application-autoscaling:DescribeScalableTargets",
          "application-autoscaling:PutScalingPolicy",
          "application-autoscaling:DeleteScalingPolicy",
          "application-autoscaling:DescribeScalingPolicies",
          "application-autoscaling:DescribeScalingActivities",
          "application-autoscaling:ListTagsForResource",
          "application-autoscaling:TagResource",
          "application-autoscaling:UntagResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "Lambda"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:GetFunctionCodeSigningConfig",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:ListVersionsByFunction",
          "lambda:PublishVersion",
          "lambda:AddPermission",
          "lambda:RemovePermission",
          "lambda:GetPolicy",
          "lambda:TagResource",
          "lambda:UntagResource",
          "lambda:ListTags",
          # Configuring Secrets Manager rotation (aws_secretsmanager_secret_rotation
          # for the Redis AUTH secret, #159) requires the caller to hold
          # lambda:InvokeFunction on the rotation function.
          "lambda:InvokeFunction"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:*"
      },
      {
        Sid    = "LambdaLayers"
        Effect = "Allow"
        Action = [
          "lambda:PublishLayerVersion",
          "lambda:GetLayerVersion",
          "lambda:DeleteLayerVersion",
          "lambda:ListLayerVersions"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:layer:*"
      },
      {
        Sid    = "ECS"
        Effect = "Allow"
        Action = [
          "ecs:*"
        ]
        Resource = "*"
      },
      {
        # Cloud Map service discovery (private DNS namespace + services) backing
        # ECS services. Namespace creation is async, so GetOperation is required
        # for Terraform to poll. Actions are not reliably ARN-addressable, so the
        # statement scopes by action and keeps Resource "*".
        Sid    = "ServiceDiscovery"
        Effect = "Allow"
        Action = [
          "servicediscovery:GetNamespace",
          "servicediscovery:ListNamespaces",
          "servicediscovery:CreatePrivateDnsNamespace",
          "servicediscovery:DeleteNamespace",
          "servicediscovery:GetService",
          "servicediscovery:ListServices",
          "servicediscovery:CreateService",
          "servicediscovery:UpdateService",
          "servicediscovery:DeleteService",
          "servicediscovery:GetOperation",
          "servicediscovery:ListTagsForResource",
          "servicediscovery:TagResource",
          "servicediscovery:UntagResource"
        ]
        Resource = "*"
      },
      {
        # Bedrock model-invocation logging configuration (account-level).
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:GetModelInvocationLoggingConfiguration",
          "bedrock:PutModelInvocationLoggingConfiguration",
          "bedrock:DeleteModelInvocationLoggingConfiguration"
        ]
        Resource = "*"
      },
      {
        # EventBridge Scheduler backing the Cognito client-secret rotation
        # reminder (portal cognito module, created when alarm_email is set so
        # enable_rotation_reminder is true). Scoped to the project/env schedule
        # name prefixes in the default schedule group.
        Sid    = "Scheduler"
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule",
          "scheduler:GetSchedule",
          "scheduler:UpdateSchedule",
          "scheduler:DeleteSchedule",
          "scheduler:ListSchedules",
          "scheduler:TagResource",
          "scheduler:UntagResource",
          "scheduler:ListTagsForResource"
        ]
        Resource = [
          "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/default/shifter-*",
          "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/default/${var.environment}-*"
        ]
      }
    ]
  })
}
