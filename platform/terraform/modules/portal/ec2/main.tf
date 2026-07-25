# EC2 Module - Django portal instance
#
# Creates:
# - EC2 instance with Docker (Amazon Linux 2023)
# - Security group (app port from ALB only)
# - IAM role and instance profile (ECR pull, Secrets Manager read, SSM)
# - CloudWatch log group for container logs

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  common_tags = merge(var.tags, {
    Module = "ec2"
  })
  iam_name_prefix = coalesce(var.iam_name_prefix, var.name_prefix)
  log_group_name  = "/portal/${var.name_prefix}"
  django_environment = (
    var.environment == "dev" ? "development" :
    var.environment == "prod" ? "production" :
    var.environment
  )
}
