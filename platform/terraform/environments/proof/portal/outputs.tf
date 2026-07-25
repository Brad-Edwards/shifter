# Portal root outputs.
#
# Proxied from the shared composition module. Names, descriptions and
# value shapes are unchanged from before the extraction (#688).

output "vpc_id" {
  description = "ID of the portal VPC"
  value       = module.portal.vpc_id
}

output "vpc_cidr" {
  description = "CIDR block of the portal VPC"
  value       = module.portal.vpc_cidr
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = module.portal.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = module.portal.private_subnet_ids
}

output "availability_zones" {
  description = "Availability zones used"
  value       = module.portal.availability_zones
}

output "private_route_table_ids" {
  description = "IDs of the per-AZ private route tables, ordered by availability_zones."
  value       = module.portal.private_route_table_ids
}

output "portal_inspection_assertion" {
  description = "Typed contract consumed by scripts/assert_portal_inspection to prove NFW route/endpoint wiring post-apply (#932)."
  value       = module.portal.portal_inspection_assertion
}

output "db_instance_id" {
  description = "DBInstanceIdentifier of the portal RDS instance (consumed by the post-apply pending-modifications check)"
  value       = module.portal.db_instance_id
}

output "guacamole_db_instance_id" {
  description = "DBInstanceIdentifier of the Guacamole RDS instance (consumed by the post-apply pending-modifications check)"
  value       = module.portal.guacamole_db_instance_id
}

output "db_instance_endpoint" {
  description = "Endpoint of the RDS instance"
  value       = module.portal.db_instance_endpoint
}

output "db_instance_address" {
  description = "Address of the RDS instance"
  value       = module.portal.db_instance_address
}

output "db_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret containing DB credentials"
  value       = module.portal.db_credentials_secret_arn
}

output "db_security_group_id" {
  description = "ID of the RDS security group"
  value       = module.portal.db_security_group_id
}

output "db_resource_id" {
  description = "Resource ID of the RDS instance (for IAM DB authentication)"
  value       = module.portal.db_resource_id
}

output "enable_autoscaling" {
  description = "Whether the portal EC2 tier is deployed as an Auto Scaling Group."
  value       = module.portal.enable_autoscaling
}

output "ec2_instance_id" {
  description = "ID of the EC2 instance (empty if ASG mode)"
  value       = module.portal.ec2_instance_id
}

output "ec2_private_ip" {
  description = "Private IP of the EC2 instance (empty if ASG mode)"
  value       = module.portal.ec2_private_ip
}

output "asg_name" {
  description = "Auto Scaling Group name (empty if single instance mode)"
  value       = module.portal.asg_name
}

output "asg_arn" {
  description = "Auto Scaling Group ARN (empty if single instance mode)"
  value       = module.portal.asg_arn
}

output "launch_template_id" {
  description = "Launch template ID (empty if single instance mode)"
  value       = module.portal.launch_template_id
}

output "ctfd_instance_id" {
  description = "ID of the CTFd instance (empty if disabled)"
  value       = module.portal.ctfd_instance_id
}

output "ctfd_private_ip" {
  description = "Private IP of the CTFd instance (empty if disabled)"
  value       = module.portal.ctfd_private_ip
}

output "ctfd_elastic_ip" {
  description = "Elastic IP of the CTFd instance (empty if disabled)"
  value       = module.portal.ctfd_elastic_ip
}

output "ctfd_url" {
  description = "Public URL for the CTFd instance (empty if disabled)"
  value       = module.portal.ctfd_url
}

output "ctfd_certbot_command" {
  description = "Certbot command to run on the CTFd instance after DNS resolves"
  value       = module.portal.ctfd_certbot_command
}

output "ctfd_ssm_connect_command" {
  description = "SSM command for shell access to the CTFd instance"
  value       = module.portal.ctfd_ssm_connect_command
}

output "ctfd_ssh_command" {
  description = "Direct SSH command for the CTFd instance"
  value       = module.portal.ctfd_ssh_command
}

output "ctfd_ssh_key_name" {
  description = "EC2 key pair name configured for the CTFd instance"
  value       = module.portal.ctfd_ssh_key_name
}

output "ctfd_security_group_id" {
  description = "Security group ID of the CTFd instance (empty if disabled)"
  value       = module.portal.ctfd_security_group_id
}

output "alb_dns_name" {
  description = "DNS name of the ALB (create CNAME pointing to this)"
  value       = module.portal.alb_dns_name
}

output "acm_validation_records" {
  description = "DNS records to create for ACM certificate validation"
  value       = module.portal.acm_validation_records
}

output "alb_https_listener_arn" {
  description = "ARN of the ALB HTTPS listener"
  value       = module.portal.alb_https_listener_arn
}

output "alb_security_group_id" {
  description = "Security group ID of the ALB"
  value       = module.portal.alb_security_group_id
}

output "portal_target_group_arn" {
  description = "ARN of the portal application target group"
  value       = module.portal.portal_target_group_arn
}

output "domain_name" {
  description = "Public portal hostname served by the ALB"
  value       = module.portal.domain_name
}

output "app_secret_arn" {
  description = "ARN of the Secrets Manager secret containing Django app secrets"
  value       = module.portal.app_secret_arn
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID"
  value       = module.portal.cognito_user_pool_id
}

output "cognito_client_id" {
  description = "Cognito user pool client ID"
  value       = module.portal.cognito_client_id
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = module.portal.cognito_domain
}

output "cognito_issuer_url" {
  description = "OIDC issuer URL"
  value       = module.portal.cognito_issuer_url
}

output "vpc_peering_connection_id" {
  description = "ID of the VPC peering connection to Range VPC"
  value       = module.portal.vpc_peering_connection_id
}

output "redis_endpoint" {
  description = "Redis primary endpoint"
  value       = module.portal.redis_endpoint
}

output "redis_port" {
  description = "Redis port"
  value       = module.portal.redis_port
}

output "engine_ecs_cluster_arn" {
  description = "ARN of the engine provisioner ECS cluster"
  value       = module.portal.engine_ecs_cluster_arn
}

output "engine_task_definition_arn" {
  description = "ARN of the engine provisioner ECS task definition"
  value       = module.portal.engine_task_definition_arn
}

output "engine_ecs_security_group_id" {
  description = "ID of the engine provisioner ECS security group"
  value       = module.portal.engine_ecs_security_group_id
}

output "engine_private_subnet_ids" {
  description = "Private subnet IDs for engine provisioner ECS tasks"
  value       = module.portal.engine_private_subnet_ids
}

output "engine_ecs_execution_role_arn" {
  description = "ARN of the engine provisioner ECS execution role"
  value       = module.portal.engine_ecs_execution_role_arn
}

output "engine_ecs_task_role_arn" {
  description = "ARN of the engine provisioner ECS task role"
  value       = module.portal.engine_ecs_task_role_arn
}

output "guacamole_target_group_arn" {
  description = "ARN of the Guacamole target group"
  value       = module.portal.guacamole_target_group_arn
}

output "guacamole_ecs_cluster_name" {
  description = "Name of the Guacamole ECS cluster"
  value       = module.portal.guacamole_ecs_cluster_name
}

output "guacd_service_name" {
  description = "Name of the guacd ECS service"
  value       = module.portal.guacd_service_name
}

output "guacamole_client_service_name" {
  description = "Name of the guacamole-client ECS service"
  value       = module.portal.guacamole_client_service_name
}

output "guacamole_json_auth_secret_arn" {
  description = "ARN of the Guacamole JSON auth secret (for Portal Django GUACAMOLE_JSON_AUTH_SECRET)"
  value       = module.portal.guacamole_json_auth_secret_arn
}
