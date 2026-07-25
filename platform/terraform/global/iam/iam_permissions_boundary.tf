# GitHub OIDC - CI Role Permissions Boundary (#253)

# ------------------------------------------------------------------------------
# Managed IAM Policies
#
# Consolidated by AWS service category (#254) to stay under AWS's hard limit of
# 10 managed policies per role. Five domain policies (compute, networking, data,
# security, management) leave headroom for future growth: a new service should
# extend an existing category, not add an eleventh attachment. The
# `check_tf_iam_role_naming` gate enforces the attachment cap. Consolidation is a
# structural move of existing statements; no permissions are broadened.
# ------------------------------------------------------------------------------

# Permissions boundary applied to every CI-created shifter-* role (#253).
# Standalone policy referenced by the security policy's iam:CreateRole condition;
# intentionally NOT attached to the github_actions role.
resource "aws_iam_policy" "ci_role_permissions_boundary" {
  # This is a permissions BOUNDARY, not a principal grant. A boundary caps the MAX permissions of
  # the CI-created service roles it bounds; effective perms are the intersection of each role's own
  # identity policy AND this boundary. The Allow*/* sets the ceiling to "anything", which the
  # DenyIamEscalation below carves IAM out of, yielding "all except IAM escalation". Without the
  # Allow* the boundary permits nothing and cripples every bounded role (e.g. firehose:PutRecord on
  # the log-shipping role). The wildcard-policy checks below assume a grant policy and do not apply
  # to boundary semantics. Risk accepted, see #44.
  # checkov:skip=CKV_AWS_286:Boundary ceiling, not a principal grant; iam:* is denied so no escalation.
  # checkov:skip=CKV_AWS_287:Boundary, not a grant; it only caps effective perms, never exposes creds.
  # checkov:skip=CKV_AWS_288:Boundary, not a grant - no data-exfil risk; it only caps, never grants.
  # checkov:skip=CKV_AWS_62:Boundary ceiling, not an administrative grant to any principal.
  # checkov:skip=CKV_AWS_63:Action="*" is required for a boundary to permit non-IAM service actions.
  # checkov:skip=CKV2_AWS_40:DenyIamEscalation removes IAM; the boundary does not allow full IAM.
  name = "shifter-${var.environment}-ci-role-boundary"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # A permissions boundary must explicitly ALLOW an action for a bounded role to use it
        # (effective = identity policy ∩ boundary). This Allow* sets the ceiling to "anything",
        # which the DenyIamEscalation below then carves IAM back out of, yielding the intended
        # "all except IAM escalation" cap. It grants nothing on its own.
        Sid      = "AllowAllExceptDenied"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
      {
        # Deny every IAM action (the anti-escalation cap, #253) EXCEPT a scoped
        # iam:PassRole to the AWS services the platform legitimately hands roles
        # to at runtime. Without this carve-out a bounded, CI-created runtime role
        # (e.g. the portal EC2 role) cannot pass the provisioner's ECS execution
        # role on ecs:RunTask, so no range can launch (issue #1452). The condition
        # only relaxes the deny for iam:PassRole calls whose iam:PassedToService is
        # one of these services; every other iam:* call (CreateRole, AttachPolicy,
        # PutRolePolicy, PassRole to any other service, and — because those calls
        # do not populate iam:PassedToService, so StringNotEquals matches on the
        # absent key — all non-PassRole IAM mutation) stays denied. The service
        # list mirrors the deploy role's own IAMPassRole grant above.
        Sid    = "DenyIamEscalation"
        Effect = "Deny"
        Action = "iam:*"
        # Carve the exact runtime-managed role/profile namespaces OUT of this
        # blanket IAM deny so the provisioner can create them at range provision
        # time. Each CreateRole grant requires THIS boundary and neither runtime
        # path can strip it. The explicit tamper denies below keep that cap in
        # place as defense-in-depth. These are namespace exceptions to a deny,
        # not grants; the provisioner identity policy remains the allow boundary.
        NotResource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-*-polaris-agent",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-*-vpn-gateway",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/shifter-${var.environment}-*-vpn-gateway"
        ]
        Condition = {
          StringNotEquals = {
            "iam:PassedToService" = [
              "ec2.amazonaws.com",
              "ecs-tasks.amazonaws.com",
              "lambda.amazonaws.com",
              "monitoring.rds.amazonaws.com",
              "vpc-flow-logs.amazonaws.com",
              "firehose.amazonaws.com",
              "logs.amazonaws.com",
              "bedrock.amazonaws.com",
              "scheduler.amazonaws.com"
            ]
          }
        }
      },
      {
        # Defense-in-depth for the polaris-agent namespace carve-out above. The
        # per-range polaris agent role (#1377) is created by the provisioner with
        # THIS boundary attached (enforced by the provisioner identity policy's
        # iam:PermissionsBoundary condition), so its effective permissions stay
        # capped even if its inline policy were broad. Explicitly deny stripping
        # or swapping that boundary on the agent roles so the cap can never be
        # removed after creation.
        Sid    = "DenyPolarisAgentBoundaryTamper"
        Effect = "Deny"
        Action = [
          "iam:PutRolePermissionsBoundary",
          "iam:DeleteRolePermissionsBoundary"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-*-polaris-agent"
      },
      {
        # The VPN role and instance-profile namespaces are excluded from the
        # blanket IAM deny only so the provisioner can manage request-owned
        # gateways. Keep the role's mandatory boundary immutable after create.
        Sid    = "DenyVpnGatewayBoundaryTamper"
        Effect = "Deny"
        Action = [
          "iam:PutRolePermissionsBoundary",
          "iam:DeleteRolePermissionsBoundary"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shifter-${var.environment}-*-vpn-gateway"
      }
    ]
  })
}
