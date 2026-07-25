# AWS Network Firewall for Range VPC Egress Filtering
#
# Filters outbound traffic from Kali and Victim instances using domain allowlists.
# - Kali: NO external access (VPC internal only via security groups)
# - Victim: XDR/XSIAM endpoints only
#
# Traffic flow: User Subnet -> Firewall -> NAT Gateway -> IGW -> Internet

# ------------------------------------------------------------------------------
# Network Firewall Policy
# ------------------------------------------------------------------------------

resource "aws_networkfirewall_firewall_policy" "this" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  name = "${var.name_prefix}-firewall-policy"

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    # Use STRICT_ORDER for predictable rule evaluation with priorities
    # Lower priority number = evaluated first
    stateful_engine_options {
      rule_order              = "STRICT_ORDER"
      stream_exception_policy = "CONTINUE"
    }

    # Rule evaluation order (STRICT_ORDER - lower priority evaluated first):
    # Priority 1: NGFW bypass - pass all from NGFW subnet
    # Priority 2-N: Victim IPs - allow HTTPS to GCP/PANW IP ranges (chunked)
    # Priority N+1: Victim domains - allow listed domains (SNI-based)
    # Priority N+2: Kali domains - allow listed domains (if configured)
    # Priority 98: NTP allow
    # Priority 100: Drop all - drop ALL unmatched traffic (default deny)
    # DNS: no public-resolver egress rule; see dns_resolver.tf

    # NGFW bypass - allow all egress for SCM/licensing (priority 1)
    dynamic "stateful_rule_group_reference" {
      for_each = var.enable_ngfw_infrastructure ? [1] : []
      content {
        resource_arn = aws_networkfirewall_rule_group.ngfw_bypass[0].arn
        priority     = 1
      }
    }

    # Victim IPs - allow HTTPS to GCP/PANW IP ranges (priorities 2, 3, 4, ...)
    dynamic "stateful_rule_group_reference" {
      for_each = aws_networkfirewall_rule_group.victim_ips
      content {
        resource_arn = stateful_rule_group_reference.value.arn
        priority     = 2 + stateful_rule_group_reference.key
      }
    }

    # Victim domains - SNI-based allowlist (priority after victim IPs)
    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.victim_domains[0].arn
      priority     = 2 + length(local.cidr_chunks) + 1
    }

    # Kali domains (priority after victim domains, only if configured)
    dynamic "stateful_rule_group_reference" {
      for_each = length(var.kali_allowed_domains) > 0 ? [1] : []
      content {
        resource_arn = aws_networkfirewall_rule_group.kali_domains[0].arn
        priority     = 2 + length(local.cidr_chunks) + 2
      }
    }

    # NTP allow - allow NTP to any (priority 98)
    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.allow_ntp[0].arn
      priority     = 98
    }

    # Drop all unmatched traffic (priority 100 - last, default deny)
    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.drop_all[0].arn
      priority     = 100
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-firewall-policy"
  })
}

# ------------------------------------------------------------------------------
# Network Firewall
# ------------------------------------------------------------------------------

# NF logging is wired via the separate aws_networkfirewall_logging_configuration
# "this" resource below, but Checkov's graph check cannot evaluate the cross-
# resource reference and flags this firewall as unlogged. See ADR-004-R11
# exception ckv2-aws-63-nf-logging-cross-resource.
resource "aws_networkfirewall_firewall" "this" {
  # checkov:skip=CKV2_AWS_63:Logging defined in aws_networkfirewall_logging_configuration "this" below.
  # checkov:skip=CKV_AWS_344:Deletion protection controlled by var.network_firewall_delete_protection (dev false / prod true). See ADR-004-R11 exception ckv-aws-344-nf-delete-protection.
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  name                = "${var.name_prefix}-firewall"
  firewall_policy_arn = aws_networkfirewall_firewall_policy.this[0].arn
  vpc_id              = aws_vpc.this.id
  delete_protection   = var.network_firewall_delete_protection

  subnet_mapping {
    subnet_id = aws_subnet.firewall[0].id
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-firewall"
  })
}

# ------------------------------------------------------------------------------
# CloudWatch Logging
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "firewall" {
  count = var.enable_network_firewall ? 1 : 0

  name              = "/aws/network-firewall/${var.name_prefix}"
  retention_in_days = var.firewall_log_retention_days
  kms_key_id        = aws_kms_key.range_vpc.arn

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-firewall-logs"
  })
}

resource "aws_networkfirewall_logging_configuration" "this" {
  count = var.enable_network_firewall ? 1 : 0

  firewall_arn = aws_networkfirewall_firewall.this[0].arn

  logging_configuration {
    log_destination_config {
      log_destination = {
        logGroup = aws_cloudwatch_log_group.firewall[0].name
      }
      log_destination_type = "CloudWatchLogs"
      log_type             = "ALERT"
    }

    log_destination_config {
      log_destination = {
        logGroup = aws_cloudwatch_log_group.firewall[0].name
      }
      log_destination_type = "CloudWatchLogs"
      log_type             = "FLOW"
    }
  }
}
