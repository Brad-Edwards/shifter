# Engine Provisioner - Gateway Load Balancer Privileges

# ------------------------------------------------------------------------------
# Task Role Policy - Gateway Load Balancer (GWLB)
# ------------------------------------------------------------------------------
# Provisioner creates GWLB infrastructure for NGFW traffic steering:
# - Gateway Load Balancer
# - Target groups with GENEVE protocol
# - Listeners

# Moved off an inline role policy to a customer-managed policy (issue #1749) for
# the same aggregate inline-policy-size reason as ec2_provisioning above.
# Permissions are unchanged from the prior inline policy.
resource "aws_iam_policy" "gwlb" {
  name        = "${var.name_prefix}-pulumi-gwlb-provisioning-managed"
  description = "Shifter provisioner Gateway Load Balancer permissions (moved off inline to stay under the role inline-policy size limit)."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ELBv2 Describe APIs require Resource = "*" per the AWS service
        # authorization reference. Actions are enumerated so the wildcard
        # statement cannot grow silently to additional read APIs.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeLoadBalancerAttributes",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetGroupAttributes",
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:DescribeListenerAttributes",
          "elasticloadbalancing:DescribeTags"
        ]
        Resource = "*"
      },
      {
        # GWLB resource creation. Scoped to Gateway Load Balancer
        # resource types and gated on Shifter ownership request tags so
        # this statement cannot create ALB/NLB resources or untagged
        # resources.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:CreateListener"
        ]
        Resource = [
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/gwy/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:listener/gwy/*/*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/*"
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
        # Existing-resource mutations. Restricted to Shifter-owned GWLB
        # resources via ELBv2 resource tags so the runtime cannot delete
        # or reconfigure load balancers it does not own.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:SetSecurityGroups",
          "elasticloadbalancing:RemoveTags"
        ]
        Resource = [
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/gwy/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:listener/gwy/*/*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/*"
        ]
        Condition = {
          StringEquals = {
            "elasticloadbalancing:ResourceTag/shifter:system"      = "shifter"
            "elasticloadbalancing:ResourceTag/shifter:environment" = var.environment
            "elasticloadbalancing:ResourceTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # Tagging at create time. Bound to the GWLB create APIs and the
        # Shifter ownership request tags so this statement cannot tag
        # arbitrary ELBv2 resources or strip ownership tags later.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:AddTags"
        ]
        Resource = [
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/gwy/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:listener/gwy/*/*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/*"
        ]
        Condition = {
          StringEquals = {
            "elasticloadbalancing:CreateAction" = [
              "CreateLoadBalancer",
              "CreateTargetGroup",
              "CreateListener"
            ]
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = "elasticloadbalancing:CreateLoadBalancer"
        Resource = "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/net/shifter-vpn-*/*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # CreateListener is authorized against the parent NLB, not the future
        # listener ARN. Require both the listener request tags and the parent
        # NLB's ownership tags so a same-account NLB outside this Shifter
        # environment cannot be used as the parent.
        Effect   = "Allow"
        Action   = "elasticloadbalancing:CreateListener"
        Resource = "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/net/shifter-vpn-*/*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/shifter:system"                        = "shifter"
            "aws:RequestTag/shifter:environment"                   = var.environment
            "aws:RequestTag/ManagedBy"                             = "terraform"
            "elasticloadbalancing:ResourceTag/shifter:system"      = "shifter"
            "elasticloadbalancing:ResourceTag/shifter:environment" = var.environment
            "elasticloadbalancing:ResourceTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = "elasticloadbalancing:CreateTargetGroup"
        Resource = "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/shifter-vpn-*/*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # Existing request-owned VPN resources remain mutable only while their
        # ownership tags identify this Shifter environment.
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:SetSecurityGroups",
          "elasticloadbalancing:RemoveTags"
        ]
        Resource = [
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/net/shifter-vpn-*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:listener/net/shifter-vpn-*/*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/shifter-vpn-*/*"
        ]
        Condition = {
          StringEquals = {
            "elasticloadbalancing:ResourceTag/shifter:system"      = "shifter"
            "elasticloadbalancing:ResourceTag/shifter:environment" = var.environment
            "elasticloadbalancing:ResourceTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # Terraform sends tags with each create request. AddTags is a dependent
        # permission and cannot be used here outside those create APIs.
        Effect = "Allow"
        Action = "elasticloadbalancing:AddTags"
        Resource = [
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:loadbalancer/net/shifter-vpn-*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:listener/net/shifter-vpn-*/*/*",
          "arn:aws:elasticloadbalancing:${local.region}:${local.account_id}:targetgroup/shifter-vpn-*/*"
        ]
        Condition = {
          StringEquals = {
            "elasticloadbalancing:CreateAction" = [
              "CreateLoadBalancer",
              "CreateTargetGroup",
              "CreateListener"
            ]
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "gwlb" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.gwlb.arn
}
