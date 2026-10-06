mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_region" { defaults = { id = "us-east-2" } }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/test" } }
}
variables {
  name_prefix                 = "test"
  environment                 = "test"
  role_name                   = "test-provisioner"
  role_id                     = "test-provisioner"
  permissions_boundary_arn    = "arn:aws:iam::123456789012:policy/boundary"
  engine_state_bucket_arn     = "arn:aws:s3:::test-state"
  engine_locks_table_arn      = "arn:aws:dynamodb:us-east-2:123456789012:table/test-locks"
  engine_secrets_kms_key_arn  = "arn:aws:kms:us-east-2:123456789012:key/test"
  secrets_manager_kms_key_arn = "arn:aws:kms:us-east-2:123456789012:key/test"
  db_resource_id              = "db-test"
  agent_s3_bucket_arn         = "arn:aws:s3:::test-agent"
  agent_s3_kms_key_arn        = "arn:aws:kms:us-east-2:123456789012:key/test-storage"
  range_vpc_id                = "vpc-mock-range"
  range_availability_zone     = "us-east-2a"
  range_instance_role_arn     = "arn:aws:iam::123456789012:role/range"
}
run "native_guest_creation_and_cleanup_remain_environment_scoped" {
  command = plan
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.native_ec2.policy).Statement :
      try(statement.Condition.StringEquals["aws:RequestTag/ManagedBy"], statement.Condition.StringEquals["ec2:ResourceTag/ManagedBy"]) == "shifter" &&
      try(statement.Condition.StringEquals["aws:RequestTag/shifter:environment"], statement.Condition.StringEquals["ec2:ResourceTag/shifter:environment"]) == "test"
    ])
    error_message = "Native EC2 permissions must require Shifter and environment ownership on every grant."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.native_ec2.policy).Statement :
      !contains(statement.Action, "iam:PassRole") && !contains(statement.Action, "ec2:CreateVpc") && !contains(statement.Action, "*")
    ])
    error_message = "Native guests do not need cloud roles or a new VPC."
  }
}

run "storage_bucket_reads_decrypt_only_its_key_through_s3" {
  command = plan
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.s3_agent.policy).Statement[1].Resource == "arn:aws:kms:us-east-2:123456789012:key/test-storage" &&
      jsondecode(aws_iam_role_policy.s3_agent.policy).Statement[1].Action == ["kms:Decrypt", "kms:GenerateDataKey"] &&
      jsondecode(aws_iam_role_policy.s3_agent.policy).Statement[1].Condition.StringEquals["kms:ViaService"] == "s3.us-east-2.amazonaws.com"
    )
    error_message = "The provisioner may use only the storage bucket's SSE-KMS key, and only through S3."
  }
}

override_resource {
  target          = aws_iam_role.range_host_model
  override_during = plan
  values = {
    arn = "arn:aws:iam::123456789012:role/test-range-host-model"
  }
}

run "range_hosts_get_only_keyless_bedrock_invocation" {
  command = plan
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.range_host_model_invoke.policy).Statement) == 1 &&
      jsondecode(aws_iam_role_policy.range_host_model_invoke.policy).Statement[0].Action == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"] &&
      alltrue([for resource in jsondecode(aws_iam_role_policy.range_host_model_invoke.policy).Statement[0].Resource :
        startswith(resource, "arn:aws:bedrock:")
      ]) &&
      jsondecode(aws_iam_role.range_host_model.assume_role_policy).Statement[0].Principal.Service == "ec2.amazonaws.com"
    )
    error_message = "The range-host role may only invoke Bedrock models (ADR-064 AWS)."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.range_host_model_pass.policy).Statement[0].Action == "iam:PassRole" &&
      jsondecode(aws_iam_role_policy.range_host_model_pass.policy).Statement[0].Condition.StringEquals["iam:PassedToService"] == "ec2.amazonaws.com"
    )
    error_message = "The provisioner may pass the range-host role only to EC2."
  }
}
