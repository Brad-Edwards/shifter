# Range VPC Firewall - Domain Rule Groups
#
# Hostname/SNI-based allowlists. IP-based rule groups live in
# firewall_rules_ips.tf; the policy that orders them lives in firewall.tf.

# ------------------------------------------------------------------------------
# Network Firewall Rule Groups
# ------------------------------------------------------------------------------

# Victim domain allowlist - XDR/XSIAM endpoints only
resource "aws_networkfirewall_rule_group" "victim_domains" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  capacity = 100
  name     = "${var.name_prefix}-victim-domains"
  type     = "STATEFUL"

  rule_group {
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
    }

    rules_source {
      rules_source_list {
        generated_rules_type = "ALLOWLIST"
        target_types         = ["TLS_SNI", "HTTP_HOST"]
        targets              = var.victim_allowed_domains
      }
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-victim-domains"
  })
}

# Kali domain allowlist - empty by default (Kali has full tools, no external access needed)
resource "aws_networkfirewall_rule_group" "kali_domains" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall && length(var.kali_allowed_domains) > 0 ? 1 : 0

  capacity = 100
  name     = "${var.name_prefix}-kali-domains"
  type     = "STATEFUL"

  rule_group {
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
    }

    rules_source {
      rules_source_list {
        generated_rules_type = "ALLOWLIST"
        target_types         = ["TLS_SNI", "HTTP_HOST"]
        targets              = var.kali_allowed_domains
      }
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-kali-domains"
  })
}

# ------------------------------------------------------------------------------
# NGFW Subnet Bypass - Allow all egress for SCM/licensing
# ------------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "ngfw_bypass" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall && var.enable_ngfw_infrastructure ? 1 : 0

  capacity = 10
  name     = "${var.name_prefix}-ngfw-bypass"
  type     = "STATEFUL"

  rule_group {
    rules_source {
      # Pass all traffic from NGFW subnet - needed for SCM registration, licensing, content updates
      rules_string = <<-EOT
        pass ip ${cidrsubnet(var.vpc_cidr, 6, 1)} any -> any any (msg:"Allow NGFW subnet all egress"; sid:1000010; rev:1;)
      EOT
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-ngfw-bypass"
  })
}
