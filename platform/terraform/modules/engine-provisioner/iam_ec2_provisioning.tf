# Engine Provisioner - EC2 Provisioning Privileges

# ------------------------------------------------------------------------------
# Task Role Policy - EC2 Provisioning
# ------------------------------------------------------------------------------

# Moved off an inline role policy to a customer-managed policy (issue #1749):
# the task role's aggregate inline-policy size exceeded AWS's 10,240-byte
# ceiling once the GWLB and OpenVPN-gateway policies were added, failing the
# portal Terraform apply. Managed policies attached to the role do not count
# toward the inline aggregate. Permissions are unchanged from the prior inline
# policy.
resource "aws_iam_policy" "ec2_provisioning" {
  name        = "${var.name_prefix}-pulumi-ec2-provisioning-managed"
  description = "Shifter provisioner EC2 lifecycle and networking permissions (moved off inline to stay under the role inline-policy size limit)."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # EC2 read and key-pair operations. Describe APIs require
        # Resource=*; key-pair names are generated per range/NGFW run.
        Effect = "Allow"
        Action = [
          "ec2:Describe*"
        ]
        Resource = "*"
      },
      {
        # Key-pair names are generated per range/NGFW run.
        Effect = "Allow"
        Action = [
          "ec2:ImportKeyPair",
          "ec2:DeleteKeyPair"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:key-pair/*"
      },
      {
        # Tagging at create time is needed for the EC2 resources provisioner
        # Terraform creates and is bound to create APIs so it cannot retag
        # arbitrary EC2 resources.
        Effect = "Allow"
        Action = [
          "ec2:CreateTags"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "ec2:CreateAction" = [
              "AllocateAddress",
              "CreateInternetGateway",
              "CreateNatGateway",
              "CreateNetworkInterface",
              "CreateRouteTable",
              "CreateSecurityGroup",
              "CreateSubnet",
              "CreateVpcEndpoint",
              "CreateVpcEndpointServiceConfiguration",
              "ImportKeyPair",
              "RunInstances"
            ]
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # EC2 instance lifecycle management for provisioner-owned instances.
        # - TerminateInstances for destroy
        # - StopInstances, StartInstances for power management
        # - ModifyInstanceAttribute for runtime changes
        # - DeleteTags for cleanup
        #
        # ModifyInstanceMetadataOptions was removed (#1377): it backed the
        # HttpPutResponseHopLimit=2 IMDS hop-limit raise, which exposed the
        # shared range-host role's SSM/S3/Bedrock credentials to any
        # container that could reach IMDS. The Polaris agent no longer
        # needs IMDS access; it receives short-lived STS credentials for
        # the per-range Bedrock agent role instead (see
        # docs/architecture/polaris-aws-agent-credentials-preflight-1377.md).
        Effect = "Allow"
        Action = [
          "ec2:TerminateInstances",
          "ec2:StopInstances",
          "ec2:StartInstances",
          "ec2:ModifyInstanceAttribute",
          "ec2:DeleteTags"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/shifter:system"      = "shifter"
            "ec2:ResourceTag/shifter:environment" = var.environment
            "ec2:ResourceTag/ManagedBy"           = "terraform"
          }
        }
      },
      {
        # Network interface operations for NGFW ENI creation
        # - CreateNetworkInterface for mgmt and data ENIs
        # - ModifyNetworkInterfaceAttribute for source_dest_check=False on data ENI
        # - DeleteNetworkInterface for cleanup
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DeleteNetworkInterface",
          "ec2:DetachNetworkInterface",
          "ec2:ModifyNetworkInterfaceAttribute"
        ]
        Resource = "*"
      },
      {
        # Full subnet lifecycle management
        # - CreateSubnet, DeleteSubnet for create/destroy
        # - ModifySubnetAttribute for map_public_ip_on_launch, etc.
        # - DescribeSubnets for state queries
        # Note: tag-on-create support is in EC2TagOnCreate.
        Effect = "Allow"
        Action = [
          "ec2:CreateSubnet",
          "ec2:DeleteSubnet",
          "ec2:ModifySubnetAttribute",
          "ec2:DescribeSubnets"
        ]
        Resource = "*"
      },
      {
        # Route Table lifecycle management
        # - CreateRouteTable, DeleteRouteTable for create/destroy
        # - CreateRoute, DeleteRoute, ReplaceRoute for route entries
        # - AssociateRouteTable, DisassociateRouteTable for subnet associations
        # - DescribeRouteTables for state queries
        Effect = "Allow"
        Action = [
          "ec2:CreateRouteTable",
          "ec2:DeleteRouteTable",
          "ec2:CreateRoute",
          "ec2:DeleteRoute",
          "ec2:ReplaceRoute",
          "ec2:AssociateRouteTable",
          "ec2:DisassociateRouteTable",
          "ec2:DescribeRouteTables"
        ]
        Resource = "*"
      },
      {
        # Security Group lifecycle management
        # - CreateSecurityGroup, DeleteSecurityGroup for create/destroy
        # - AuthorizeSecurityGroupIngress/Egress for inbound/outbound rules
        # - RevokeSecurityGroupIngress/Egress for rule removal
        # Note: DescribeSecurityGroups covered by Describe* in EC2InstanceOperations
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupEgress"
        ]
        Resource = "*"
      },
      {
        # Internet Gateway lifecycle management
        # Required for routing traffic to/from the internet
        Effect = "Allow"
        Action = [
          "ec2:CreateInternetGateway",
          "ec2:DeleteInternetGateway",
          "ec2:AttachInternetGateway",
          "ec2:DetachInternetGateway",
          "ec2:DescribeInternetGateways"
        ]
        Resource = "*"
      },
      {
        # Elastic IP lifecycle management
        # Required for static public IPs on instances/NAT gateways
        Effect = "Allow"
        Action = [
          "ec2:AllocateAddress",
          "ec2:ReleaseAddress",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
          "ec2:DescribeAddresses"
        ]
        Resource = "*"
      },
      {
        # NAT Gateway lifecycle management
        # Required for private subnet outbound internet access
        Effect = "Allow"
        Action = [
          "ec2:CreateNatGateway",
          "ec2:DeleteNatGateway",
          "ec2:DescribeNatGateways"
        ]
        Resource = "*"
      },
      {
        # PassRole for range instances and NGFW instances
        # compact() filters out empty strings when NGFW is not enabled
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = compact([
          var.range_instance_role_arn,
          var.ngfw_instance_role_arn
        ])
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ec2_provisioning" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ec2_provisioning.arn
}
