# Range Bedrock endpoint contract (ADR-064 AWS, #2529).
#
# Native range guests reach the Bedrock runtime endpoint by security-group
# reference. The endpoint therefore carries its own group, distinct from the
# shared SSM/Secrets Manager/STS endpoint group, and that group is published so
# the provisioner can grant egress to exactly it.
#
# Credential-free: mock_provider synthesizes all AWS data/resources.
# Run with:
#   terraform -chdir=platform/terraform/modules/range/vpc test -filter=tests/model_endpoint.tftest.hcl

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-2a", "us-east-2b", "us-east-2c"]
    }
  }

  mock_resource "aws_networkfirewall_firewall" {
    defaults = {
      firewall_status = [{
        sync_states = [{
          availability_zone = "us-east-2a"
          attachment = [{
            endpoint_id = "vpce-firewallmock0000000"
          }]
        }]
      }]
    }
  }
}

variables {
  name_prefix              = "test-range"
  vpc_cidr                 = "10.1.0.0/16"
  portal_vpc_cidr          = "10.0.0.0/16"
  tags                     = { Environment = "test" }
  agent_s3_bucket          = "test-agent-bucket"
  environment              = "test"
  permissions_boundary_arn = "arn:aws:iam::123456789012:policy/test-boundary"
  enable_network_firewall  = true
  victim_allowed_cidrs     = []
}

override_resource {
  target          = aws_security_group.model_endpoint
  override_during = plan
  values = {
    id = "sg-0000000000000model"
  }
}

override_resource {
  target          = aws_security_group.ssm_endpoints
  override_during = plan
  values = {
    id = "sg-00000000000000ssm"
  }
}

run "bedrock_endpoint_has_its_own_published_group" {
  command = plan

  assert {
    condition = (
      one(aws_vpc_endpoint.bedrock_runtime.security_group_ids) == "sg-0000000000000model" &&
      one(aws_vpc_endpoint.sts.security_group_ids) == "sg-00000000000000ssm" &&
      output.model_endpoint_security_group_id == "sg-0000000000000model"
    )
    error_message = "The Bedrock endpoint must carry only its own published security group."
  }

  assert {
    condition = (
      aws_security_group_rule.model_endpoint_https_from_vpc.type == "ingress" &&
      aws_security_group_rule.model_endpoint_https_from_vpc.from_port == 443 &&
      aws_security_group_rule.model_endpoint_https_from_vpc.to_port == 443 &&
      one(aws_security_group_rule.model_endpoint_https_from_vpc.cidr_blocks) == "10.1.0.0/16" &&
      aws_security_group_rule.model_endpoint_https_from_vpc.security_group_id == "sg-0000000000000model"
    )
    error_message = "The Bedrock endpoint group admits only HTTPS from the range VPC."
  }
}
