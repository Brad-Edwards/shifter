# Portal EC2 - Messaging Privileges
#
# Range-event publication, SQS consume/publish, the SQS KMS grant, and SES.

# Range-events publish (#476 outbox drainer + reconciler). These workers run
# under this EC2 role and publish range status events to the range-events SNS
# topic; without sns:Publish (+ kms on the CMK-encrypted topic) the outbox never
# drains and ranges stay stuck "provisioning" in the portal forever.
resource "aws_iam_role_policy" "range_events_publish" {
  name = "range-events-publish"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RangeEventsSnsPublish"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.range_events_topic_arn
      },
      {
        Sid    = "RangeEventsKms"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt"
        ]
        Resource = var.sqs_kms_key_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "sqs_consume" {
  name = "sqs-consume"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = var.sqs_queue_arns
      }
    ]
  })
}

resource "aws_iam_role_policy" "sqs_publish" {
  name = "sqs-publish"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = var.sqs_queue_arns
      }
    ]
  })
}

resource "aws_iam_role_policy" "sqs_kms" {
  name = "sqs-kms-access"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = var.sqs_kms_key_arn
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
            "kms:ViaService"    = "sqs.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ses_send" {
  count = var.enable_ses ? 1 : 0

  name = "ses-send"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = var.ses_domain_identity_arn
      },
      {
        Effect   = "Allow"
        Action   = "ses:GetSendQuota"
        Resource = "*"
      }
    ]
  })
}
