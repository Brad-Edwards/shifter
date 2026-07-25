# Engine Provisioner - EC2 RunInstances Authorization

# Keep EC2 launch authorization in a customer-managed policy rather than an
# inline role policy. The EC2 lifecycle inline policy is already close to AWS's
# role inline-policy size ceiling, and RunInstances needs separate statements
# because EC2 evaluates images, implicit ENIs, root volumes, and instances with
# different condition-key contexts.
resource "aws_iam_policy" "ec2_run_instances" {
  name        = "${var.name_prefix}-pulumi-ec2-run-instances-managed"
  description = "Allows the Shifter provisioner to launch range and NGFW instances with scoped dependent resources."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Instance creation is restricted by the runtime Terraform tags that
        # the provisioner applies to every managed range/NGFW instance.
        Effect = "Allow"
        Action = [
          "ec2:RunInstances"
        ]
        Resource = [
          "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        ]
        Condition = {
          StringEquals = {
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # Root EBS volumes created by RunInstances do not expose the
        # aws:RequestTag context during EC2 authorization. Limit them to the
        # configured range AZ and require encrypted, newly-created volumes.
        Effect = "Allow"
        Action = [
          "ec2:RunInstances"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:volume/*"
        Condition = {
          StringEquals = {
            "ec2:AvailabilityZone" = var.range_availability_zone
          }
          Bool = {
            "aws:ResourceBeingCreated" = "true"
            "ec2:Encrypted"            = "true"
          }
        }
      },
      {
        # AMIs used by the range provisioner are published into this account
        # (including the Polaris golden AMIs). Scope RunInstances image use to
        # account-owned images rather than allowing arbitrary public AMIs.
        Effect = "Allow"
        Action = [
          "ec2:RunInstances"
        ]
        Resource = "arn:aws:ec2:${local.region}::image/*"
        Condition = {
          StringEquals = {
            "ec2:Owner" = local.account_id
          }
        }
      },
      {
        # Dependent network resources for RunInstances. AWS evaluates implicit
        # instance ENIs separately from the tagged instance/volume resources,
        # so request-tag conditions do not match them. Constrain these
        # dependent authorizations to the configured Range VPC instead.
        Effect = "Allow"
        Action = [
          "ec2:RunInstances"
        ]
        Resource = [
          "arn:aws:ec2:${local.region}:${local.account_id}:network-interface/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:subnet/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:security-group/*"
        ]
        Condition = {
          ArnEquals = {
            "ec2:Vpc" = "arn:aws:ec2:${local.region}:${local.account_id}:vpc/${var.range_vpc_id}"
          }
        }
      },
      {
        # NGFW launches use a provisioner-created, Shifter-tagged key pair.
        Effect = "Allow"
        Action = [
          "ec2:RunInstances"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:key-pair/*"
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

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ec2_run_instances" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ec2_run_instances.arn
}
