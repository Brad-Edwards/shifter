mock_provider "aws" {}

override_resource {
  target = aws_iam_role.cluster
  values = {
    arn = "arn:aws:iam::123456789012:role/shifter-test-cluster"
  }
}

override_resource {
  target = aws_iam_role.node
  values = {
    arn = "arn:aws:iam::123456789012:role/shifter-test-node"
  }
}

override_resource {
  target = aws_iam_role.vpc_flow_logs
  values = {
    arn = "arn:aws:iam::123456789012:role/shifter-test-vpc-flow-logs"
  }
}

override_resource {
  target = aws_iam_role.workload["cni"]
  values = {
    arn = "arn:aws:iam::123456789012:role/shifter-test-cni"
  }
}

override_resource {
  target = aws_kms_key.cluster
  values = {
    arn = "arn:aws:kms:us-east-2:123456789012:key/11111111-1111-1111-1111-111111111111"
  }
}

override_resource {
  target = aws_kms_key.secrets
  values = {
    arn = "arn:aws:kms:us-east-2:123456789012:key/22222222-2222-2222-2222-222222222222"
  }
}

override_resource {
  target = aws_cloudwatch_log_group.vpc_flow
  values = {
    arn = "arn:aws:logs:us-east-2:123456789012:log-group:/aws/vpc/shifter-test/flow"
  }
}

override_resource {
  target = aws_cloudwatch_log_group.waf
  values = {
    arn = "arn:aws:logs:us-east-2:123456789012:log-group:aws-waf-logs-shifter-test-ingress"
  }
}

override_resource {
  target = aws_wafv2_web_acl.ingress
  values = {
    arn = "arn:aws:wafv2:us-east-2:123456789012:regional/webacl/shifter-test-ingress/11111111-1111-1111-1111-111111111111"
  }
}

override_resource {
  target = aws_subnet.private["us-east-2a"]
  values = {
    id = "subnet-mock-private-a"
  }
}

override_resource {
  target = aws_subnet.private["us-east-2b"]
  values = {
    id = "subnet-mock-private-b"
  }
}

override_resource {
  target = aws_eks_cluster.this
  values = {
    arn      = "arn:aws:eks:us-east-2:123456789012:cluster/shifter-test"
    endpoint = "https://example.test"
    certificate_authority = [{
      data = "dGVzdA=="
    }]
    identity = [{
      oidc = [{
        issuer = "https://oidc.eks.us-east-2.amazonaws.com/id/EXAMPLE"
      }]
    }]
  }
}

override_resource {
  target = aws_launch_template.node
  values = {
    id             = "lt-11111111111111111"
    latest_version = 1
  }
}

variables {
  environment          = "test"
  aws_region           = "us-east-2"
  cluster_name         = "shifter-test"
  deployment_role_arn  = "arn:aws:iam::123456789012:role/shifter-test-deploy"
  vpc_cidr             = "10.42.0.0/16"
  availability_zones   = ["us-east-2a", "us-east-2b"]
  private_subnet_cidrs = ["10.42.0.0/20", "10.42.16.0/20"]
  public_subnet_cidrs  = ["10.42.128.0/24", "10.42.129.0/24"]
  kubernetes_version   = "1.31"
  domain_name          = "shifter.test.example.com"
  oidc_thumbprints     = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  node_instance_types  = ["m7i.large"]
  workload_identities = {
    cni = {
      namespace       = "kube-system"
      service_account = "aws-node"
      policy_arns     = ["arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"]
    }
    portal = {
      namespace       = "shifter-platform"
      service_account = "shifter-portal"
      policy_arns     = ["arn:aws:iam::123456789012:policy/shifter-test-portal"]
    }
  }
  secret_names = [
    "database",
    "django",
  ]
  tags = {
    Environment = "test"
    Project     = "shifter"
  }
}

run "security_contract" {
  command = apply

  assert {
    condition     = aws_eks_cluster.this.vpc_config[0].endpoint_private_access && !aws_eks_cluster.this.vpc_config[0].endpoint_public_access
    error_message = "The EKS API endpoint must be private and must not expose public access."
  }

  assert {
    condition     = !contains([for attachment in aws_iam_role_policy_attachment.node : attachment.policy_arn], "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy")
    error_message = "The CNI policy must use exact service-account identity, not node-wide credentials."
  }

  assert {
    condition     = aws_launch_template.node.block_device_mappings[0].ebs[0].encrypted && aws_launch_template.node.metadata_options[0].http_tokens == "required"
    error_message = "Managed nodes must use encrypted disks and require IMDSv2."
  }

  assert {
    condition     = length(aws_eks_cluster.this.encryption_config) == 1 && aws_eks_cluster.this.encryption_config[0].resources == toset(["secrets"])
    error_message = "The EKS cluster must encrypt Kubernetes secrets with a customer-managed KMS key."
  }

  assert {
    condition     = aws_eks_cluster.this.access_config[0].authentication_mode == "API" && !aws_eks_cluster.this.access_config[0].bootstrap_cluster_creator_admin_permissions
    error_message = "Cluster access must use explicit access entries without an implicit creator-admin grant."
  }

  assert {
    condition     = aws_kms_key.cluster.enable_key_rotation && aws_kms_key.secrets.enable_key_rotation
    error_message = "Cluster and secret-store KMS keys must rotate automatically."
  }

  assert {
    condition     = toset(aws_eks_node_group.this.subnet_ids) == toset([for subnet in aws_subnet.private : subnet.id])
    error_message = "The managed node group must use only private subnets."
  }

  assert {
    condition     = aws_iam_openid_connect_provider.cluster.client_id_list == toset(["sts.amazonaws.com"])
    error_message = "IRSA trust must use the STS audience."
  }

  assert {
    condition     = strcontains(aws_iam_role.workload["portal"].assume_role_policy, "system:serviceaccount:shifter-platform:shifter-portal")
    error_message = "Workload identity must bind an exact namespace and service-account subject."
  }

  assert {
    condition     = !strcontains(aws_iam_role.workload["portal"].assume_role_policy, "system:serviceaccount:*")
    error_message = "Workload identity must not trust wildcard service-account subjects."
  }

  assert {
    condition     = alltrue([for secret in aws_secretsmanager_secret.platform : secret.kms_key_id == aws_kms_key.secrets.arn])
    error_message = "Platform secret stores must use the dedicated customer-managed KMS key."
  }

  assert {
    condition     = aws_acm_certificate.ingress.validation_method == "DNS" && aws_wafv2_web_acl.ingress.scope == "REGIONAL"
    error_message = "AWS ingress prerequisites must include a DNS-validated regional certificate and regional WAF."
  }

  assert {
    condition     = aws_cloudwatch_log_group.cluster.retention_in_days >= 365 && aws_cloudwatch_log_group.vpc_flow.retention_in_days >= 365 && aws_cloudwatch_log_group.waf.retention_in_days >= 365
    error_message = "EKS, VPC flow, and WAF logs must be retained for at least one year."
  }

  assert {
    condition     = aws_flow_log.this.traffic_type == "ALL" && aws_flow_log.this.log_destination == aws_cloudwatch_log_group.vpc_flow.arn
    error_message = "The EKS VPC must publish all flow records to the encrypted log group."
  }

  assert {
    condition     = length(aws_default_security_group.this.ingress) == 0 && length(aws_default_security_group.this.egress) == 0
    error_message = "The EKS VPC default security group must deny all traffic."
  }

  assert {
    condition     = contains([for rule in aws_wafv2_web_acl.ingress.rule : rule.name], "AWSManagedRulesKnownBadInputsRuleSet")
    error_message = "The ingress WAF must enable AWS managed known-bad-input protection."
  }

  assert {
    condition     = aws_wafv2_web_acl_logging_configuration.ingress.resource_arn == aws_wafv2_web_acl.ingress.arn
    error_message = "The ingress WAF must publish request logs."
  }
}
