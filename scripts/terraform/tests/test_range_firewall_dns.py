"""Static invariants for range VPC DNS egress hardening (#1172)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIREWALL_TF = REPO_ROOT / "platform/terraform/modules/range/vpc/firewall.tf"
DNS_RESOLVER_TF = REPO_ROOT / "platform/terraform/modules/range/vpc/dns_resolver.tf"
VARIABLES_TF = REPO_ROOT / "platform/terraform/modules/range/vpc/variables.tf"


def test_firewall_tf_has_no_public_recursive_dns_allow_rules() -> None:
    content = FIREWALL_TF.read_text()
    assert "8.8.8.8" not in content, "firewall.tf must not allow DNS egress to public resolvers"
    assert "allow_dns" not in content, "public-recursive allow_dns rule group must be removed"


def test_dns_resolver_tf_defines_split_horizon_firewall() -> None:
    content = DNS_RESOLVER_TF.read_text()
    assert "aws_route53_resolver_firewall_rule_group" in content
    assert "aws_route53_resolver_firewall_config" in content
    assert "aws_route53_resolver_firewall_rule_group_association" in content
    assert '"BLOCK"' in content
    assert "block_unlisted" in content
    variables = VARIABLES_TF.read_text()
    assert ".amazonaws.com" in variables
    assert "range_dns_allowed_domains" in content
