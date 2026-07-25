# Engine Provisioner CI - GitHub OIDC Trust and Deploy Identities
#
# Provider, roles, and outputs. The managed policies these roles attach are in
# iam_policy_*.tf; the attachments and their migration moved blocks are in
# iam_attachments.tf. All files in this directory form one Terraform module,
# so resource addresses are unaffected by the file layout (#688).

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Environment name (dev, prod, or proof)"
  type        = string
  validation {
    condition     = contains(["dev", "prod", "proof"], var.environment)
    error_message = "Environment must be 'dev', 'prod', or 'proof'."
  }
}

variable "github_org" {
  description = "GitHub organization"
  type        = string
  default     = "Brad-Edwards"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "shifter"
}

# Get current AWS account ID
data "aws_caller_identity" "current" {}

# GitHub OIDC Provider
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1b511abead59c6ce207077c0bf0e0043b1382612"]

  tags = {
    Name    = "github-actions-oidc"
    Project = "shifter"
  }
}

# IAM Role for GitHub Actions
resource "aws_iam_role" "github_actions" {
  name = "github-actions-shifter-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${var.github_repo}:*"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "github-actions-shifter-${var.environment}"
    Project     = "shifter"
    Environment = var.environment
  }
}

# ------------------------------------------------------------------------------
# Least-privilege base-image-pipeline role (#1656)
#
# The packer.yml base `build` job must not run under the broad github_actions
# deploy role above: that role legitimately passes portal EC2, ECS, Lambda,
# RDS-monitoring, and every range role to AWS services, so it cannot
# simultaneously prove that a base-image verification instance may receive ONLY
# the range instance role. This dedicated principal IS the IAM boundary the
# workflow's inline ref gate is not:
#
#   * OIDC trust is pinned to the EXACT protected-branch subjects
#     (refs/heads/dev, refs/heads/main) - never repo:...:* - so a pull-request
#     or feature-branch job cannot assume it even if it can dispatch the
#     workflow.
#   * iam:PassRole is scoped to the EXACT env range role
#     (shifter-${var.environment}-range-range-instance) passed to
#     ec2.amazonaws.com and nothing else, so the fresh-boot verifier can launch
#     a range-profile instance while the role cannot exfiltrate a more-privileged
#     profile (AWS warns role tags / profile-name checks are not a PassRole
#     boundary).
#   * EC2 is the amazon-ebs builder + verifier + always() cleanup action set;
#     SSM is the verifier (DescribeInstanceInformation / SendCommand /
#     GetCommandInvocation) plus publication of the /shifter/ami/* base pointers
#     only. No IAM mutation, Secrets Manager, arbitrary Parameter Store, or
#     scenario-artifact access.
#
# The exact-subject and exact-range-role invariants are enforced by
# scripts/check_tf_iam_role_naming (ADR-004-R22). Binding design note:
# docs/architecture/packer-base-build-privilege-boundary-preflight-1656.md.
# ------------------------------------------------------------------------------
resource "aws_iam_role" "github_actions_image" {
  name = "github-actions-shifter-${var.environment}-image"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            # EXACT protected-branch subjects only (never repo:...:*). The base
            # build runs from dev|main (the workflow's inline ref gate); a
            # feature-branch or pull-request subject receives no AWS role.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/dev",
              "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"
            ]
          }
        }
      }
    ]
  })

  tags = {
    Name        = "github-actions-shifter-${var.environment}-image"
    Project     = "shifter"
    Environment = var.environment
  }
}

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "github_actions_role_arn" {
  description = "ARN of the IAM role for GitHub Actions (add to GitHub secrets as AWS_ROLE_ARN)"
  value       = aws_iam_role.github_actions.arn
}

output "github_actions_image_role_arn" {
  description = "ARN of the least-privilege base-image-pipeline role for packer.yml base builds (add to GitHub secrets as AWS_IMAGE_ROLE_ARN_<ENV>) (#1656)"
  value       = aws_iam_role.github_actions_image.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider"
  value       = aws_iam_openid_connect_provider.github.arn
}
