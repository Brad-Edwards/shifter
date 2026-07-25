# Portal EC2 - Launch Template and Single-Instance Mode
#
# The ASG consumes this launch template; aws_instance.this is the alternative
# single-instance deployment mode. Scaling lives in autoscaling.tf.

# ------------------------------------------------------------------------------
# Launch Template (for ASG mode)
# ------------------------------------------------------------------------------

resource "aws_launch_template" "this" {
  count = var.enable_autoscaling ? 1 : 0

  name_prefix   = "${var.name_prefix}-lt-"
  image_id      = var.ec2_ami_id
  instance_type = var.instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.this.name
  }

  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.this.id]
  }

  user_data = base64gzip(templatefile("${path.module}/user_data.sh", {
    aws_region                 = var.aws_region
    django_environment         = local.django_environment
    cloud_provider             = var.cloud_provider
    ecr_repository_url         = var.ecr_repository_url
    log_group_name             = local.log_group_name
    ssm_parameter_store_prefix = var.ssm_parameter_store_prefix
    lifecycle_hook_name        = "${var.name_prefix}-launch-hook"
    name_prefix                = var.name_prefix
    docker_stop_timeout        = var.docker_stop_timeout
    worker_health_monitor_b64  = base64encode(file("${path.module}/worker-health/shifter-worker-health.sh"))
    worker_health_service_b64  = base64encode(file("${path.module}/worker-health/shifter-worker-health.service"))
    worker_health_timer_b64    = base64encode(file("${path.module}/worker-health/shifter-worker-health.timer"))
  }))

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = var.root_volume_size
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  monitoring {
    enabled = true
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name        = "${var.name_prefix}-ec2"
      ShifterRole = "shifter-platform"
    })
  }

  tag_specifications {
    resource_type = "volume"
    tags = merge(local.common_tags, {
      Name = "${var.name_prefix}-ec2-vol"
    })
  }

  tags = merge(local.common_tags, {
    Name = "${var.name_prefix}-launch-template"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# ------------------------------------------------------------------------------
# EC2 Instance (for single instance mode)
# ------------------------------------------------------------------------------

resource "aws_instance" "this" {
  count = var.enable_autoscaling ? 0 : 1

  ami                    = var.ec2_ami_id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.this.name
  monitoring             = true
  ebs_optimized          = true

  user_data_base64 = base64gzip(templatefile("${path.module}/user_data.sh", {
    aws_region                 = var.aws_region
    django_environment         = local.django_environment
    cloud_provider             = var.cloud_provider
    ecr_repository_url         = var.ecr_repository_url
    log_group_name             = local.log_group_name
    ssm_parameter_store_prefix = var.ssm_parameter_store_prefix
    lifecycle_hook_name        = ""
    name_prefix                = var.name_prefix
    docker_stop_timeout        = var.docker_stop_timeout
    worker_health_monitor_b64  = base64encode(file("${path.module}/worker-health/shifter-worker-health.sh"))
    worker_health_service_b64  = base64encode(file("${path.module}/worker-health/shifter-worker-health.service"))
    worker_health_timer_b64    = base64encode(file("${path.module}/worker-health/shifter-worker-health.timer"))
  }))

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # Enforce IMDSv2
    http_put_response_hop_limit = 2          # Allow containers to access IMDS
    instance_metadata_tags      = "enabled"
  }

  tags = merge(local.common_tags, {
    Name        = "${var.name_prefix}-ec2"
    ShifterRole = "shifter-platform"
  })

  lifecycle {
    ignore_changes = [ami]
  }
}
