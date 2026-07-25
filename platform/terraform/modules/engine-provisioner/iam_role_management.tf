# Engine Provisioner - Dynamic Role Management
#
# Per-range Polaris Bedrock agent roles (#1377) and request-owned OpenVPN
# gateway roles, both namespace-scoped.

# ------------------------------------------------------------------------------
# Task Role Policy - Polaris Agent Role Management (#1377)
# ------------------------------------------------------------------------------
# The engine provisioner owns the per-range Polaris Bedrock agent role
# (shifter/engine/provisioner/terraform/modules/range/iam.tf) through the
# same ECS task role that applies the rest of the per-range Terraform.
# Scoped to the shifter-${environment}-*-polaris-agent namespace only; the
# target role is never attached to EC2 (no instance profile), so
# iam:PassRole is intentionally not granted here.

resource "aws_iam_role_policy" "polaris_agent_role_management" {
  name = "polaris-agent-role-management"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # iam:PermissionsBoundary is only present in the request context
        # for IAM calls that set a boundary (CreateRole,
        # PutRolePermissionsBoundary, ...); it does not exist for
        # PutRolePolicy. Bundling a StringEquals condition on this key
        # into a statement that also grants PutRolePolicy would deny
        # every PutRolePolicy call outright, because a StringEquals
        # condition on an absent context key evaluates to false. Scoping
        # the condition to CreateRole alone still forces every role
        # created in this namespace to carry the boundary: the task role
        # below has no iam:PutRolePermissionsBoundary /
        # iam:DeleteRolePermissionsBoundary grant, so the boundary set at
        # creation can never be changed or removed through this role.
        Sid      = "CreatePolarisAgentRoleWithBoundary"
        Effect   = "Allow"
        Action   = "iam:CreateRole"
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-polaris-agent"
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = var.permissions_boundary_arn
          }
        }
      },
      {
        Sid    = "ManagePolarisAgentRole"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          # The AWS provider's read-after-create and read-before-destroy of
          # aws_iam_role always call ListAttachedRolePolicies and
          # ListInstanceProfilesForRole (even though the agent role uses only an
          # inline policy and no instance profile), so terraform apply/destroy
          # fails without them. Read-only; scoped to the agent role namespace.
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:ListRoleTags"
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-polaris-agent"
      }
    ]
  })
}

# Request-owned OpenVPN gateway roles are separate from participant hosts and
# can read exactly one generation-specific server identity. The provisioner may
# create them only with the installation permissions boundary and may pass them
# only to EC2.
resource "aws_iam_role_policy" "vpn_gateway_role_management" {
  name = "vpn-gateway-role-management"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CreateVpnGatewayRoleWithBoundary"
        Effect   = "Allow"
        Action   = "iam:CreateRole"
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-vpn-gateway"
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = var.permissions_boundary_arn
          }
        }
      },
      {
        Sid    = "ManageVpnGatewayRole"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:ListRoleTags"
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-vpn-gateway"
      },
      {
        Sid      = "UseOnlySsmCorePolicy"
        Effect   = "Allow"
        Action   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-vpn-gateway"
        Condition = {
          ArnEquals = {
            "iam:PolicyARN" = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
          }
        }
      },
      {
        Sid    = "ManageVpnGatewayInstanceProfile"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile",
          "iam:DeleteInstanceProfile",
          "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile",
          "iam:GetInstanceProfile",
          "iam:TagInstanceProfile",
          "iam:UntagInstanceProfile"
        ]
        Resource = "arn:aws:iam::${local.account_id}:instance-profile/shifter-${var.environment}-*-vpn-gateway"
      },
      {
        Sid      = "PassVpnGatewayRoleOnlyToEc2"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${local.account_id}:role/shifter-${var.environment}-*-vpn-gateway"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      }
    ]
  })
}
