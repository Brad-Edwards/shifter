# GitHub OIDC - Policy Attachments and Consolidation Migration (#254)
#
# All five attachments live here so the AWS 10-managed-policy cap stays
# reviewable in one place; check_tf_iam_role_naming also aggregates the count
# across this directory so spreading attachments across siblings cannot evade
# the cap (#688).

# ------------------------------------------------------------------------------
# Policy Attachments
# ------------------------------------------------------------------------------

resource "aws_iam_role_policy_attachment" "compute" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.compute.arn
}

resource "aws_iam_role_policy_attachment" "networking" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.networking.arn
}

resource "aws_iam_role_policy_attachment" "data" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.data.arn
}

resource "aws_iam_role_policy_attachment" "security" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.security.arn
}

resource "aws_iam_role_policy_attachment" "management" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.management.arn
}

# ------------------------------------------------------------------------------
# Migration: safe detach-before-attach rollout for the #254 consolidation
#
# The role already holds AWS's hard maximum of 10 managed-policy attachments.
# Going to 5 attachments cannot be done by introducing 5 brand-new attachment
# resources while the 10 old ones are orphaned: Terraform does not guarantee it
# destroys orphaned attachments before creating new ones, so the role would
# momentarily exceed 10 attachments mid-apply and AWS would reject it with
# LimitExceededException.
#
# These `moved` blocks repoint five existing attachment addresses onto the five
# consolidated policies instead. Because each address already exists in state,
# Terraform treats the policy_arn change as an in-place REPLACEMENT (policy_arn
# is ForceNew), which under the default lifecycle is destroy-before-create:
# the old policy is detached, then the new one attached, on the same address -
# the count never rises. The remaining five old attachment resources
# (core_infrastructure, elb_acm, lambda_ops, secrets_kms, network_firewall) are
# absent from the config and are destroyed (detached), taking the role from 10
# down to 5. The role therefore stays at or below 10 attachments at every point
# of the apply, with no net-new attachment addresses created.
#
# On a fresh environment with no prior state these blocks are no-ops and the
# five attachments are created normally (nothing to exceed). The blocks are
# one-time migration aids; they may be removed once every environment's global
# IAM state has been applied.
# ------------------------------------------------------------------------------

moved {
  from = aws_iam_role_policy_attachment.ec2_instances
  to   = aws_iam_role_policy_attachment.compute
}

moved {
  from = aws_iam_role_policy_attachment.vpc_networking
  to   = aws_iam_role_policy_attachment.networking
}

moved {
  from = aws_iam_role_policy_attachment.rds
  to   = aws_iam_role_policy_attachment.data
}

moved {
  from = aws_iam_role_policy_attachment.iam_scoped
  to   = aws_iam_role_policy_attachment.security
}

moved {
  from = aws_iam_role_policy_attachment.ssm_cognito
  to   = aws_iam_role_policy_attachment.management
}
