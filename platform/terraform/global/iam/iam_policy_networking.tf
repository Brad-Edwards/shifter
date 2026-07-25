# GitHub OIDC - Networking Category Policy (#254)

# Networking: VPC, ELB, ACM, WAFv2, Network Firewall
# checkov:skip=CKV_AWS_355:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_290:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_289:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# checkov:skip=CKV_AWS_287:CI/CD requires broad networking permissions for infrastructure management. Risk accepted, see #44
# NOTE: Not best practice. Project in rapid development - velocity impact of permissions errors
# and size of inline policies outweigh need for pure least privilege. Risk accepted.
resource "aws_iam_policy" "networking" {
  name = "shifter-${var.environment}-networking"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VPC"
        Effect = "Allow"
        Action = [
          "ec2:*Vpc*",
          "ec2:*Subnet*",
          "ec2:*RouteTable*",
          "ec2:*Route",
          "ec2:*InternetGateway*",
          "ec2:*NatGateway*",
          "ec2:*Address*",
          "ec2:*SecurityGroup*",
          "ec2:*Tags",
          "ec2:Describe*",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:CreateFlowLogs",
          "ec2:DeleteFlowLogs",
          "ec2:DescribeFlowLogs"
        ]
        Resource = "*"
      },
      {
        Sid      = "ELB"
        Effect   = "Allow"
        Action   = ["elasticloadbalancing:*"]
        Resource = "*"
      },
      {
        Sid      = "ACM"
        Effect   = "Allow"
        Action   = ["acm:*"]
        Resource = "*"
      },
      {
        Sid    = "WAFv2"
        Effect = "Allow"
        Action = [
          "wafv2:CreateWebACL",
          "wafv2:DeleteWebACL",
          "wafv2:GetWebACL",
          "wafv2:UpdateWebACL",
          "wafv2:ListWebACLs",
          "wafv2:AssociateWebACL",
          "wafv2:DisassociateWebACL",
          "wafv2:GetWebACLForResource",
          "wafv2:ListResourcesForWebACL",
          "wafv2:ListTagsForResource",
          "wafv2:TagResource",
          "wafv2:UntagResource",
          "wafv2:DescribeManagedRuleGroup",
          "wafv2:ListAvailableManagedRuleGroups",
          "wafv2:GetLoggingConfiguration",
          "wafv2:PutLoggingConfiguration",
          "wafv2:DeleteLoggingConfiguration",
          "wafv2:ListLoggingConfigurations"
        ]
        Resource = "*"
      },
      {
        Sid    = "NetworkFirewall"
        Effect = "Allow"
        Action = [
          "network-firewall:CreateFirewall",
          "network-firewall:DeleteFirewall",
          "network-firewall:DescribeFirewall",
          "network-firewall:UpdateFirewallDeleteProtection",
          "network-firewall:UpdateFirewallDescription",
          "network-firewall:UpdateFirewallPolicy",
          "network-firewall:UpdateFirewallPolicyChangeProtection",
          "network-firewall:UpdateSubnetChangeProtection",
          "network-firewall:AssociateFirewallPolicy",
          "network-firewall:DisassociateSubnets",
          "network-firewall:AssociateSubnets",
          "network-firewall:CreateFirewallPolicy",
          "network-firewall:DeleteFirewallPolicy",
          "network-firewall:DescribeFirewallPolicy",
          "network-firewall:UpdateFirewallPolicy",
          "network-firewall:CreateRuleGroup",
          "network-firewall:DeleteRuleGroup",
          "network-firewall:DescribeRuleGroup",
          "network-firewall:UpdateRuleGroup",
          "network-firewall:ListFirewalls",
          "network-firewall:ListFirewallPolicies",
          "network-firewall:ListRuleGroups",
          "network-firewall:TagResource",
          "network-firewall:UntagResource",
          "network-firewall:ListTagsForResource",
          "network-firewall:DescribeLoggingConfiguration",
          "network-firewall:UpdateLoggingConfiguration"
        ]
        Resource = "*"
      },
      {
        # Route 53 Resolver DNS Firewall + query logging for the range VPC
        # egress controls (#1171 zero-egress range, #1172 close DNS exfil,
        # modules/range/vpc/dns_resolver.tf). None of these APIs support
        # resource-level scoping, so the statement uses Resource "*".
        Sid    = "Route53ResolverDNSFirewall"
        Effect = "Allow"
        Action = [
          "route53resolver:CreateFirewallDomainList",
          "route53resolver:DeleteFirewallDomainList",
          "route53resolver:GetFirewallDomainList",
          "route53resolver:ListFirewallDomainLists",
          "route53resolver:UpdateFirewallDomains",
          "route53resolver:ListFirewallDomains",
          "route53resolver:ImportFirewallDomains",
          "route53resolver:CreateFirewallRuleGroup",
          "route53resolver:DeleteFirewallRuleGroup",
          "route53resolver:GetFirewallRuleGroup",
          "route53resolver:ListFirewallRuleGroups",
          "route53resolver:CreateFirewallRule",
          "route53resolver:DeleteFirewallRule",
          "route53resolver:UpdateFirewallRule",
          "route53resolver:ListFirewallRules",
          "route53resolver:AssociateFirewallRuleGroup",
          "route53resolver:DisassociateFirewallRuleGroup",
          "route53resolver:GetFirewallRuleGroupAssociation",
          "route53resolver:ListFirewallRuleGroupAssociations",
          "route53resolver:UpdateFirewallRuleGroupAssociation",
          "route53resolver:GetFirewallConfig",
          "route53resolver:UpdateFirewallConfig",
          "route53resolver:ListFirewallConfigs",
          "route53resolver:CreateResolverQueryLogConfig",
          "route53resolver:DeleteResolverQueryLogConfig",
          "route53resolver:GetResolverQueryLogConfig",
          "route53resolver:ListResolverQueryLogConfigs",
          "route53resolver:AssociateResolverQueryLogConfig",
          "route53resolver:DisassociateResolverQueryLogConfig",
          "route53resolver:GetResolverQueryLogConfigAssociation",
          "route53resolver:ListResolverQueryLogConfigAssociations",
          "route53resolver:TagResource",
          "route53resolver:UntagResource",
          "route53resolver:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        # Route53 hosted zones. Cloud Map / Service Discovery private DNS
        # namespaces (module.guacamole.aws_service_discovery_private_dns_namespace)
        # create a backing private hosted zone, so the CI role needs route53
        # hosted-zone actions in addition to servicediscovery:*. Only surfaces on
        # a from-zero standup: established accounts already have the namespace, so
        # no CreateHostedZone call is made (#1425). CreateHostedZone has no
        # resource-level scoping, so Resource = "*".
        Sid    = "Route53HostedZones"
        Effect = "Allow"
        Action = [
          "route53:CreateHostedZone",
          "route53:GetHostedZone",
          "route53:GetHostedZoneCount",
          "route53:ListHostedZones",
          "route53:ListHostedZonesByName",
          "route53:DeleteHostedZone",
          "route53:UpdateHostedZoneComment",
          "route53:AssociateVPCWithHostedZone",
          "route53:DisassociateVPCFromHostedZone",
          "route53:ChangeResourceRecordSets",
          "route53:ListResourceRecordSets",
          "route53:GetChange",
          "route53:ListTagsForResource",
          "route53:ChangeTagsForResource"
        ]
        Resource = "*"
      }
    ]
  })
}
