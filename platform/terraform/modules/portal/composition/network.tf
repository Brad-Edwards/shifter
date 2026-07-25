# Portal Composition - network
#
# VPC, ALB, portal<->range peering and routes, and target-service SG ingress.
#
# Sibling file of the same Terraform module, so resource addresses are
# unaffected by this layout (#688).


# ------------------------------------------------------------------------------
# VPC
# ------------------------------------------------------------------------------

module "vpc" {
  source = "../vpc"

  name_prefix              = local.name_prefix
  iam_name_prefix          = local.iam_name_prefix
  permissions_boundary_arn = local.ci_role_permissions_boundary_arn
  vpc_cidr                 = var.vpc_cidr
  az_count                 = var.az_count
  enable_nat_gateway       = var.enable_nat_gateway
  tags                     = var.tags

  # Phase 5: VPC Flow Logs
  enable_flow_logs   = var.enable_vpc_flow_logs
  log_retention_days = var.log_retention_days

  # Portal east-west inspection (#122)
  enable_portal_inspection    = var.enable_portal_inspection
  enable_log_aggregation      = var.enable_log_aggregation
  firewall_log_retention_days = var.firewall_log_retention_days

  # Network Firewall lifecycle (mirrors db_deletion_protection root-var / tfvars convention)
  portal_inspection_delete_protection = var.portal_inspection_delete_protection
}

# ------------------------------------------------------------------------------
# ALB (created first, target attached after EC2)
# ------------------------------------------------------------------------------

module "alb" {
  source = "../alb"

  name_prefix                = local.name_prefix
  vpc_id                     = module.vpc.vpc_id
  public_subnet_ids          = module.vpc.public_subnet_ids
  domain_name                = var.domain_name
  app_port                   = var.app_port
  health_check_path          = var.health_check_path
  enable_stickiness          = var.enable_autoscaling
  enable_deletion_protection = var.alb_enable_deletion_protection

  # Long-lived connection lifecycle (#931): explicit idle timeout + portal
  # target drain.
  idle_timeout_seconds         = var.alb_idle_timeout_seconds
  deregistration_delay_seconds = var.portal_deregistration_delay_seconds

  # Phase 5: ALB Access Logs and WAF Logging
  enable_access_logs      = var.enable_alb_access_logs
  logs_bucket_name        = var.enable_alb_access_logs ? local.alb_access_logs_bucket_name : ""
  logs_bucket_policy_id   = var.enable_alb_access_logs ? module.log_aggregation.alb_logs_bucket_policy_id : ""
  enable_waf_logging      = var.enable_waf_logging
  waf_log_destination_arn = var.enable_waf_logging ? module.log_aggregation.waf_firehose_arn : ""

  tags = var.tags
}

# ------------------------------------------------------------------------------
# ALB Target Attachment (single instance mode only - ASG attaches automatically)
# ------------------------------------------------------------------------------

resource "aws_lb_target_group_attachment" "portal" {
  count = var.enable_autoscaling ? 0 : 1

  target_group_arn = module.alb.target_group_arn
  target_id        = module.ec2.instance_id
  port             = var.app_port
}

# ------------------------------------------------------------------------------
# VPC Peering: Portal <-> Range
# Enables SSH connectivity from Portal to Range instances for Terminal UI
# ------------------------------------------------------------------------------

resource "aws_vpc_peering_connection" "portal_to_range" {
  vpc_id      = module.vpc.vpc_id
  peer_vpc_id = var.range_vpc_id
  auto_accept = true # Same account, same region

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-to-range-peering"
  })
}

# Route from Portal private subnets to Range VPC via peering (per-AZ).
resource "aws_route" "portal_to_range" {
  count = length(module.vpc.private_route_table_ids)

  route_table_id            = module.vpc.private_route_table_ids[count.index]
  destination_cidr_block    = var.range_vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.portal_to_range.id
}

# Route from Range private subnets to Portal VPC via peering
resource "aws_route" "range_to_portal" {
  route_table_id            = var.range_private_route_table_id
  destination_cidr_block    = module.vpc.vpc_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.portal_to_range.id
}

# When portal inspection is enabled, ALB health checks and user traffic reach
# the private targets through the Network Firewall endpoint. AWS documents that
# security-group references do not allow traffic across a routed middlebox (the
# flow is split source->middlebox and middlebox->destination), so the inspected
# ALB->target path needs a CIDR rule in addition to the module SG-to-SG rules.
# That CIDR is scoped to the ALB ingress tier ONLY (`alb_ingress_subnet_cidrs`),
# never the whole public tier: standalone public workloads (e.g. CTFd) live in
# the separate public-workload tier and so cannot reach Django:8000 /
# Guacamole:8080 directly (#911 NET-2 / #933).
# See https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall-with-vpc-routing-enhancements/
resource "aws_security_group_rule" "portal_app_from_alb_subnets" {
  type              = "ingress"
  from_port         = var.app_port
  to_port           = var.app_port
  protocol          = "tcp"
  cidr_blocks       = module.vpc.alb_ingress_subnet_cidrs
  security_group_id = module.ec2.security_group_id
  description       = "HTTP from ALB ingress subnets through inspection"
}

resource "aws_security_group_rule" "guacamole_client_from_alb_subnets" {
  type              = "ingress"
  from_port         = 8080
  to_port           = 8080
  protocol          = "tcp"
  cidr_blocks       = module.vpc.alb_ingress_subnet_cidrs
  security_group_id = module.guacamole.guacamole_client_security_group_id
  description       = "HTTP from ALB ingress subnets through inspection"
}
