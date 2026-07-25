# GitHub OIDC - Base Image Pipeline Inline Policy (#1656)

# Packer amazon-ebs AMI/instance/snapshot operations cannot be meaningfully
# ARN-scoped, so the EC2 statement keeps Resource "*" and constrains by action to
# the documented builder+verifier+cleanup set (never ec2:*). The PassRole and SSM
# statements ARE tightly scoped. The checkov skips below cover only unavoidable
# Packer EC2 findings; the least-privilege boundary is the action list plus the
# exact PassRole/SSM-publish resources (#1656).
resource "aws_iam_role_policy" "image_pipeline" {
  # checkov:skip=CKV_AWS_355:Packer EC2 build/verify actions are not ARN-scopable; scoped by action, not ec2:*. Risk accepted, see #1656.
  # checkov:skip=CKV_AWS_290:EC2 build/verify needs Resource=*; PassRole is the exact range role and SSM publish is /shifter/ami/* PutParameter only. See #1656.
  # checkov:skip=CKV_AWS_287:ec2:GetPasswordData is required for Packer Windows base builds (WinRM admin password) and is not ARN-scopable; the role grants no other credential-exposure action. Risk accepted, see #1656.
  name = "shifter-${var.environment}-image-pipeline"
  role = aws_iam_role.github_actions_image.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # amazon-ebs builder + fresh-boot verifier + always() cleanup. RebootInstances
        # covers the verifier's reboot-survival check (base-image-verify.sh).
        Sid    = "Ec2ImageBuildAndVerify"
        Effect = "Allow"
        Action = [
          "ec2:AttachVolume",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:CopyImage",
          "ec2:CreateImage",
          "ec2:CreateKeyPair",
          "ec2:CreateSecurityGroup",
          "ec2:CreateSnapshot",
          "ec2:CreateTags",
          "ec2:CreateVolume",
          "ec2:DeleteKeyPair",
          "ec2:DeleteSecurityGroup",
          "ec2:DeleteSnapshot",
          "ec2:DeleteVolume",
          "ec2:DeregisterImage",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeImageAttribute",
          "ec2:DescribeImages",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeKeyPairs",
          "ec2:DescribeRegions",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSnapshots",
          "ec2:DescribeSubnets",
          "ec2:DescribeTags",
          "ec2:DescribeVolumes",
          "ec2:DescribeVpcs",
          "ec2:DetachVolume",
          "ec2:GetPasswordData",
          "ec2:ModifyImageAttribute",
          "ec2:ModifyInstanceAttribute",
          "ec2:ModifySnapshotAttribute",
          "ec2:RebootInstances",
          "ec2:RegisterImage",
          "ec2:RunInstances",
          "ec2:StopInstances",
          "ec2:TerminateInstances"
        ]
        Resource = "*"
      },
      {
        # The fresh-boot verifier launches a candidate with the range instance
        # profile (base-image-verify.sh --iam-instance-profile). This role may
        # pass ONLY the exact env range role to EC2 - not shifter-*, not
        # *-range-instance, not any other role - so a tampered verify profile
        # cannot exfiltrate a more-privileged role.
        Sid      = "PassRangeInstanceRoleToEc2Only"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-range-range-instance"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      },
      {
        # Verifier SSM checks: confirm the candidate registers with SSM and can
        # resolve DNS via Run Command. List actions are not ARN-scopable.
        Sid    = "SsmVerifyInstanceInformation"
        Effect = "Allow"
        Action = [
          "ssm:DescribeInstanceInformation",
          "ssm:GetCommandInvocation"
        ]
        Resource = "*"
      },
      {
        # SendCommand scoped to instances in this account and the two documents
        # the verifier invokes (Linux + Windows DNS resolution probe).
        Sid    = "SsmVerifyRunCommand"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*",
          "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
          "arn:aws:ssm:${var.aws_region}::document/AWS-RunPowerShellScript"
        ]
      },
      {
        # Publish the validated base AMI id to the runtime pointer. Write-only
        # (the build only ever put-parameters /shifter/ami/<type>; it never reads
        # a parameter), scoped to the /shifter/ami/* namespace - not arbitrary
        # Parameter Store, and no read grant that would trip credential-exposure.
        Sid      = "PublishBaseAmiPointer"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/shifter/ami/*"
      }
    ]
  })
}
