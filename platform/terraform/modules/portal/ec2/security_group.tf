# Portal EC2 - Security Group

# ------------------------------------------------------------------------------
# Security Group
# ------------------------------------------------------------------------------

resource "aws_security_group" "this" {
  name        = "${var.name_prefix}-ec2-sg"
  description = "Security group for Django EC2 instance"
  vpc_id      = var.vpc_id

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-ec2-sg"
  })
}

resource "aws_security_group_rule" "app_from_alb" {
  type                     = "ingress"
  from_port                = var.app_port
  to_port                  = var.app_port
  protocol                 = "tcp"
  source_security_group_id = var.alb_security_group_id
  security_group_id        = aws_security_group.this.id
  description              = "App traffic from ALB"
}

resource "aws_security_group_rule" "egress_all" { #tfsec:ignore:aws-ec2-no-public-egress -- instance requires outbound for ECR, SES, S3, SSM, and external APIs
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"] # NOSONAR
  security_group_id = aws_security_group.this.id
  description       = "Allow all outbound (ECR, SES, S3, SSM, external APIs)"
}
