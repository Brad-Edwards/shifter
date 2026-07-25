# Engine Provisioner - SSM Parameter and Run Command Privileges

# ------------------------------------------------------------------------------
# Task Role Policy - SSM Parameters (DC Config)
# ------------------------------------------------------------------------------
# DC component creates SSM parameters to store domain config (credentials, etc.)
# that domain members retrieve during setup.

resource "aws_iam_role_policy" "ssm_parameters" {
  name = "ssm-parameters"
  role = aws_iam_role.ecs_task.id

  # Permissions based on AWS docs for SSM Parameter Store:
  # https://docs.aws.amazon.com/systems-manager/latest/userguide/sysman-paramstore-access.html
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          # Create/Update
          "ssm:PutParameter",
          # Read
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParameterHistory",
          # Delete
          "ssm:DeleteParameter",
          # Tagging
          "ssm:AddTagsToResource",
          "ssm:ListTagsForResource",
          "ssm:RemoveTagsFromResource"
        ]
        Resource = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/shifter/${var.environment}/range/*"
      },
      {
        # Read-only access to AMI parameters (set by Packer builds)
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/shifter/ami/*"
      },
      {
        # DescribeParameters required by Terraform for metadata lookup
        # Must be * resource per AWS API requirements
        Effect   = "Allow"
        Action   = "ssm:DescribeParameters"
        Resource = "*"
      },
      {
        # KMS permissions for SecureString parameters
        # Uses AWS managed key for SSM via service condition
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${local.region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - SSM Run Command (for DC setup orchestration)
# ------------------------------------------------------------------------------
# Engine provisioner uses SSM Run Command to orchestrate DC setup:
# - Install AD DS feature
# - Reboot and wait for instance
# - Promote to Domain Controller
# - Verify AD DS is running

resource "aws_iam_role_policy" "ssm_run_command" {
  name = "ssm-run-command"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # SendCommand instance authorization. Scoped to Shifter range guest
        # instances via SSM resource-tag conditions so a provisioner compromise
        # cannot run commands on portal, GitHub-runner, or other EC2 instances.
        # Range guests carry shifter:system / shifter:environment plus the
        # range-specific shifter:range_id tag (see the range Terraform module);
        # portal and runner instances do not, so they are denied. The generic
        # ownership tags alone are insufficient because non-range infrastructure
        # can share them, hence the required shifter:range_id presence.
        Effect = "Allow"
        Action = [
          "ssm:SendCommand"
        ]
        Resource = [
          "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        ]
        Condition = {
          StringEquals = {
            "ssm:resourceTag/shifter:system"      = "shifter"
            "ssm:resourceTag/shifter:environment" = var.environment
          }
          Null = {
            # The range-id tag must be present on the target instance.
            "ssm:resourceTag/shifter:range_id" = "false"
          }
        }
      },
      {
        # SendCommand document authorization. A SendCommand call must be
        # authorized for both the instance resource(s) AND the document
        # resource, so this unconditioned document statement cannot re-broaden
        # instance targeting. Pinned to the two AWS-managed documents already in
        # use; do not widen.
        Effect = "Allow"
        Action = [
          "ssm:SendCommand"
        ]
        Resource = [
          "arn:aws:ssm:${local.region}::document/AWS-RunPowerShellScript",
          "arn:aws:ssm:${local.region}::document/AWS-RunShellScript"
        ]
      },
      {
        # Result polling is a distinct permission from command execution. AWS
        # requires Resource=* for these read APIs; keep them separate and
        # enumerated rather than folding them into the SendCommand statement.
        Effect = "Allow"
        Action = [
          "ssm:GetCommandInvocation",
          "ssm:ListCommandInvocations"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:DescribeInstanceInformation"
        ]
        Resource = "*"
      },
      {
        # Reboot is EC2, not SSM command execution. Scope it with the same
        # ownership tag conditions as the EC2 instance lifecycle statement so it
        # cannot reboot instances the provisioner does not own.
        Effect = "Allow"
        Action = [
          "ec2:RebootInstances"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/shifter:system"      = "shifter"
            "ec2:ResourceTag/shifter:environment" = var.environment
            "ec2:ResourceTag/ManagedBy"           = "terraform"
          }
        }
      }
    ]
  })
}
