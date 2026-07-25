"""Static invariants for range VPC DNS egress hardening (#1172)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RANGE_VPC_DIR = REPO_ROOT / "platform/terraform/modules/range/vpc"
DNS_RESOLVER_TF = RANGE_VPC_DIR / "dns_resolver.tf"
VARIABLES_TF = RANGE_VPC_DIR / "variables.tf"


def _range_vpc_hcl() -> str:
    """Concatenate every ``*.tf`` in the range VPC module.

    Terraform evaluates all sibling files in a directory as one module, so the
    DNS-egress invariant is a property of the module, not of any single file.
    Reading the whole directory keeps the check working when rule groups are
    reorganized across sibling files instead of silently passing on a file that
    no longer holds the rules (#688).
    """
    return "\n".join(path.read_text() for path in sorted(RANGE_VPC_DIR.glob("*.tf")))


def test_range_vpc_has_no_public_recursive_dns_allow_rules() -> None:
    content = _range_vpc_hcl()
    assert "8.8.8.8" not in content, (
        "range VPC module must not allow DNS egress to public resolvers"
    )
    assert "allow_dns" not in content, "public-recursive allow_dns rule group must be removed"


def test_dns_resolver_tf_defines_split_horizon_firewall() -> None:
    content = DNS_RESOLVER_TF.read_text()
    assert "aws_route53_resolver_firewall_rule_group" in content
    assert "aws_route53_resolver_firewall_config" in content
    assert "aws_route53_resolver_firewall_rule_group_association" in content
    assert '"BLOCK"' in content
    assert "block_unlisted" in content
    assert "range_dns_allowed_domains" in content
    aws_service_suffix = "." + "amazonaws" + "." + "com"
    default_entry = f'"{aws_service_suffix}",'
    assert any(line.strip() == default_entry for line in VARIABLES_TF.read_text().splitlines()), (
        "range_dns_allowed_domains default must include AWS service suffix for bootstrap DNS"
    )
