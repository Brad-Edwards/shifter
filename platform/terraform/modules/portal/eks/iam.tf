locals {
  workload_policy_attachments = merge([
    for identity_name, identity in var.workload_identities : {
      for policy_arn in identity.policy_arns :
      "${identity_name}:${policy_arn}" => {
        identity_name = identity_name
        policy_arn    = policy_arn
      }
    }
  ]...)
  workload_secret_access = {
    for identity_name, identity in var.workload_identities :
    identity_name => identity
    if length(identity.secret_names) > 0
  }
  workload_object_read_access = {
    for identity_name, identity in var.workload_identities :
    identity_name => identity.object_read_arns
    if length(identity.object_read_arns) > 0
  }
}

resource "aws_iam_role" "cluster" {
  name = "${var.cluster_name}-cluster"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role_policy" "cluster_kms" {
  name = "cluster-kms"
  role = aws_iam_role.cluster.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "kms:CreateGrant",
        "kms:DescribeKey",
      ]
      Resource = aws_kms_key.cluster.arn
      Condition = {
        Bool = {
          "kms:GrantIsForAWSResource" = "true"
        }
      }
    }]
  })
}

resource "aws_iam_role" "node" {
  name = "${var.cluster_name}-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
  ])

  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_iam_openid_connect_provider" "cluster" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = var.oidc_thumbprints

  tags = var.tags
}

resource "aws_iam_role" "workload" {
  for_each = var.workload_identities

  name = "${var.cluster_name}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.cluster.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
          "${replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${each.value.namespace}:${each.value.service_account}"
        }
      }
    }]
  })

  tags = merge(var.tags, {
    KubernetesNamespace      = each.value.namespace
    KubernetesServiceAccount = each.value.service_account
  })
}

resource "aws_iam_role_policy_attachment" "workload" {
  for_each = local.workload_policy_attachments

  role       = aws_iam_role.workload[each.value.identity_name].name
  policy_arn = each.value.policy_arn
}

resource "aws_iam_role_policy" "workload_secrets" {
  for_each = local.workload_secret_access

  name = "exact-secret-access"
  role = aws_iam_role.workload[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
        ]
        Resource = [
          for secret_name in each.value.secret_names :
          aws_secretsmanager_secret.platform[secret_name].arn
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.secrets.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy" "workload_object_read" {
  for_each = local.workload_object_read_access

  name = "exact-object-read"
  role = aws_iam_role.workload[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = sort(tolist(each.value))
    }]
  })
}
