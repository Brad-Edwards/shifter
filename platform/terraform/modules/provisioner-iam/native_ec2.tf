# Native RAES resources use an explicit manager identity instead of claiming
# Terraform ownership. Existing dependent-resource RunInstances grants still
# constrain AMIs to this account, encrypted disks to the range zone, and NICs to
# the range VPC. The only instance profile native guests may carry is the
# Bedrock-invoke range-host profile below (ADR-064 AWS).
resource "aws_iam_policy" "native_ec2" {
  name = "${var.name_prefix}-native-ec2-managed"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ec2:CreateTags"]
        Resource = [for kind in ["instance", "volume", "network-interface", "subnet", "security-group", "route-table"] :
          "arn:aws:ec2:${local.region}:${local.account_id}:${kind}/*"
        ]
        Condition = {
          StringEquals = {
            "ec2:CreateAction"                   = ["RunInstances", "CreateSubnet", "CreateSecurityGroup", "CreateRouteTable"]
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "shifter"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:RunInstances"]
        Resource = ["arn:aws:ec2:${local.region}:${local.account_id}:instance/*"]
        Condition = {
          StringEquals = {
            "aws:RequestTag/shifter:system"      = "shifter"
            "aws:RequestTag/shifter:environment" = var.environment
            "aws:RequestTag/ManagedBy"           = "shifter"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:TerminateInstances", "ec2:StopInstances", "ec2:StartInstances", "ec2:DeleteVolume"]
        Resource = [for kind in ["instance", "volume"] : "arn:aws:ec2:${local.region}:${local.account_id}:${kind}/*"]
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/shifter:system"      = "shifter"
            "ec2:ResourceTag/shifter:environment" = var.environment
            "ec2:ResourceTag/ManagedBy"           = "shifter"
          }
        }
      }
    ]
  })
  tags = var.tags
}
resource "aws_iam_role_policy_attachment" "native_ec2" {
  role       = var.role_name
  policy_arn = aws_iam_policy.native_ec2.arn
}

# Default-on keyless model access for native range hosts (ADR-064 AWS, #2529).
# The role can only invoke Bedrock models; it holds no other AWS authority.
# Guests reach it through IMDSv2 with a hop limit of 1, so containers on a host
# cannot read it directly. The provisioner attaches it only when a range has no
# model-broker guest path (the two are mutually exclusive per ADR-064).
resource "aws_iam_role" "range_host_model" {
  name                 = "${var.name_prefix}-range-host-model"
  permissions_boundary = var.permissions_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "range_host_model_invoke" {
  name = "bedrock-invoke"
  role = aws_iam_role.range_host_model.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "InvokeModels"
      Effect = "Allow"
      Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      # Cross-region inference profiles route to foundation models in several
      # regions, so the foundation-model grant is region-wildcarded.
      Resource = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
        "arn:aws:bedrock:*:${local.account_id}:application-inference-profile/*",
      ]
    }]
  })
}

resource "aws_iam_instance_profile" "range_host_model" {
  name = "${var.name_prefix}-range-host-model"
  role = aws_iam_role.range_host_model.name
  tags = var.tags
}

resource "aws_iam_role_policy" "range_host_model_pass" {
  name = "range-host-model-pass"
  role = var.role_id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "iam:PassRole"
      Resource  = aws_iam_role.range_host_model.arn
      Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } }
    }]
  })
}
