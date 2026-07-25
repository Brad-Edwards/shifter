# Range VPC Firewall - IP and Default-Deny Rule Groups
#
# CIDR allowlists sourced from var.victim_allowed_cidrs (rendered from
# shifter.yaml settings.range_egress), plus NTP allow and the default drop.
# Domain rule groups live in firewall_rules_domains.tf.

# ------------------------------------------------------------------------------
# Block Direct IP Connections (no hostname/SNI bypass)
# ------------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "block_ip_sni" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  capacity = 10
  name     = "${var.name_prefix}-block-ip-sni"
  type     = "STATEFUL"

  rule_group {
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
      ip_sets {
        key = "EXTERNAL_NET"
        ip_set {
          definition = ["0.0.0.0/0"]
        }
      }
    }

    rules_source {
      # Suricata rule to reject TLS connections where SNI is an IP address
      # This prevents bypassing domain allowlist by connecting directly to IPs
      rules_string = <<-EOT
        reject tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:"."; pcre:"/^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$/"; msg:"Blocked: IP address used as TLS SNI"; sid:1000001; rev:1;)
      EOT
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-block-ip-sni"
  })
}

# ------------------------------------------------------------------------------
# IP-based Allowlist (GCP ranges for PANW services)
# Split into multiple rule groups due to AWS 8192 char rule limit
#
# IMPORTANT: Reducing the number of CIDR chunks (victim_allowed_cidrs) requires
# manual intervention. Terraform cannot properly order the policy update before
# deleting orphaned rule groups because AWS Network Firewall requires the policy
# to be updated first. Lifecycle blocks have not resolved this issue historically.
#
# Manual fix when reducing chunks:
#   1. Update the firewall policy via AWS CLI to remove the rule group references:
#      aws network-firewall update-firewall-policy --firewall-policy-name <name> \
#        --update-token <token> --firewall-policy '<json without orphaned refs>'
#   2. Run terraform apply to delete the orphaned rule groups
# ------------------------------------------------------------------------------

locals {
  # Split CIDRs into chunks of 300 to stay under AWS rule length limit
  cidr_chunk_size = 300
  cidr_chunks     = var.enable_network_firewall && length(var.victim_allowed_cidrs) > 0 ? chunklist(var.victim_allowed_cidrs, local.cidr_chunk_size) : []
}

resource "aws_networkfirewall_rule_group" "victim_ips" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? length(local.cidr_chunks) : 0

  capacity = 1000 # Each CIDR uses ~1 capacity unit
  name     = "${var.name_prefix}-victim-ips-${count.index + 1}"
  type     = "STATEFUL"

  rule_group {
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
      ip_sets {
        key = "ALLOWED_IPS"
        ip_set {
          definition = local.cidr_chunks[count.index]
        }
      }
    }

    rules_source {
      # Allow TCP 443 to GCP/PANW IPs (chunk ${count.index + 1})
      rules_string = <<-EOT
        pass tcp $HOME_NET any -> $ALLOWED_IPS 443 (msg:"Allow HTTPS to PANW/GCP IPs chunk ${count.index + 1}"; sid:${2000001 + count.index}; rev:1;)
      EOT
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-victim-ips-${count.index + 1}"
  })
}

# DNS egress is handled in dns_resolver.tf: hosts use AmazonProvidedDNS inside
# the VPC; Route 53 Resolver DNS Firewall allowlists scenario/bootstrap suffixes
# and blocks unknown external names. No UDP/TCP 53 egress to public resolvers.

# ------------------------------------------------------------------------------
# NTP Allow Rule (UDP 123 - required for time sync)
# ------------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "allow_ntp" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  capacity = 10
  name     = "${var.name_prefix}-allow-ntp"
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
      rules_string = <<-EOT
        pass udp $HOME_NET any -> any 123 (msg:"Allow NTP"; sid:1000030; rev:1;)
      EOT
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-allow-ntp"
  })
}

# ------------------------------------------------------------------------------
# Drop All Unmatched Traffic (default deny)
# ------------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "drop_all" {
  encryption_configuration {
    type   = "CUSTOMER_KMS"
    key_id = aws_kms_key.range_vpc.arn
  }

  count = var.enable_network_firewall ? 1 : 0

  capacity = 10
  name     = "${var.name_prefix}-drop-all"
  type     = "STATEFUL"

  rule_group {
    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
      ip_sets {
        key = "EXTERNAL_NET"
        ip_set {
          definition = ["0.0.0.0/0"]
        }
      }
    }

    rules_source {
      # Drop ALL outbound traffic that wasn't explicitly allowed by previous rules
      # This enforces the allowlist - only traffic to allowed domains/IPs/DNS passes
      # CRITICAL: This blocks all protocols and ports, not just HTTP/HTTPS
      rules_string = <<-EOT
        drop ip $HOME_NET any -> $EXTERNAL_NET any (msg:"Drop all unmatched egress"; sid:9999999; rev:1;)
      EOT
    }

    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-drop-all"
  })
}
