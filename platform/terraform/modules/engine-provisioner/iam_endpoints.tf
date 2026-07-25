# Engine Provisioner - VPC Endpoint and Bootstrap Object Privileges

# ------------------------------------------------------------------------------
# Task Role Policy - VPC Endpoints
# ------------------------------------------------------------------------------
# Provisioner creates:
# - VPC Endpoint Services for GWLB connectivity from ranges (gwlb_component.py)
# - VPC Endpoints (GatewayLoadBalancer type) in range subnets (network.py)

resource "aws_iam_role_policy" "vpc_endpoints" {
  name = "vpc-endpoints"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # VPC Endpoint Service operations (for GWLB service exposure)
        Effect = "Allow"
        Action = [
          "ec2:CreateVpcEndpointServiceConfiguration",
          "ec2:DeleteVpcEndpointServiceConfigurations",
          "ec2:ModifyVpcEndpointServiceConfiguration",
          "ec2:ModifyVpcEndpointServicePermissions",
          "ec2:DescribeVpcEndpointServiceConfigurations",
          "ec2:DescribeVpcEndpointServicePermissions",
          "ec2:AcceptVpcEndpointConnections",
          "ec2:RejectVpcEndpointConnections"
        ]
        Resource = "*"
      },
      {
        # VPC Endpoint operations (for GWLB endpoints in range subnets)
        Effect = "Allow"
        Action = [
          "ec2:CreateVpcEndpoint",
          "ec2:DeleteVpcEndpoints",
          "ec2:ModifyVpcEndpoint",
          "ec2:DescribeVpcEndpoints"
        ]
        Resource = "*"
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# Task Role Policy - Runtime Writes
# ------------------------------------------------------------------------------
# Provisioner needs write access to bootstrap/* prefix for NGFW init-cfg.txt,
# authcodes, and other bootstrap configuration files. It also publishes range
# lifecycle events to SNS. Keep these together so SCP-constrained accounts that
# require inline policies stay under IAM's aggregate inline-role policy limit.

resource "aws_iam_role_policy" "s3_bootstrap" {
  name = "s3-bootstrap-write"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectTagging"
        ]
        Resource = "${var.agent_s3_bucket_arn}/bootstrap/*"
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.sns_topic_arn
      },
      {
        # The range-events topic is encrypted with the portal messaging CMK.
        # SNS Publish calls fail unless the publishing task role can use that
        # CMK through the SNS service path.
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey"
        ]
        Resource = var.sns_kms_key_arn
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = local.account_id
            "kms:ViaService"    = "sns.${local.region}.amazonaws.com"
          }
        }
      }
    ]
  })
}
