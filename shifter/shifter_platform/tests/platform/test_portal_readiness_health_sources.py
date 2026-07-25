"""Portal readiness and instance replacement health-source invariants (#919, #1639)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EC2_DIR = REPO_ROOT / "platform" / "terraform" / "modules" / "portal" / "ec2"
EC2_VARIABLES_TF = EC2_DIR / "variables.tf"


def _ec2_module_hcl() -> str:
    """Concatenate every ``*.tf`` in the portal EC2 module.

    Terraform evaluates all sibling files in a directory as one module, so
    these structural invariants are properties of the module rather than of
    any single file. Reading the whole directory keeps them working when the
    module is reorganized across sibling files instead of silently passing on
    a file that no longer holds the resource (#688).
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(EC2_DIR.glob("*.tf")))


def test_portal_asg_health_check_tracks_alb_target_group_readiness() -> None:
    """#1639: the portal ASG uses ELB health so an instance refresh converges on
    real ALB target-group readiness instead of EC2 status checks.

    The prior #919 choice of ``health_check_type = "EC2"`` left instance refreshes
    stuck on "insufficient data to evaluate its health with Amazon EC2" for the
    warmup window. The type is now an environment-owned Terraform variable that
    defaults to ELB (the ALB target group is attached), so a refresh completes
    when the portal is actually serving.
    """
    main_tf = _ec2_module_hcl()
    variables_tf = EC2_VARIABLES_TF.read_text(encoding="utf-8")

    # Health-check type is env-owned and no longer hardcoded to EC2.
    assert "health_check_type         = var.health_check_type" in main_tf
    assert 'health_check_type         = "EC2"' not in main_tf
    # The ALB target group is attached, so ELB health reflects real readiness.
    assert "target_group_arns         = [var.target_group_arn]" in main_tf
    # The variable exists and defaults to ELB.
    assert 'variable "health_check_type"' in variables_tf
    assert 'default     = "ELB"' in variables_tf
