output "range_host_instance_profile_arn" {
  description = "Instance profile native range hosts carry for keyless Bedrock invocation (ADR-064 AWS)."
  value       = aws_iam_instance_profile.range_host_model.arn
}
