# Provider data sources owned by the composition.
#
# terraform_remote_state reads stay in the environment roots (they are
# backend-coupled); their outputs arrive here as typed variables (#688).

data "aws_ssm_parameter" "kali_ami" {
  name = "/shifter/ami/kali"
}

data "aws_ssm_parameter" "victim_ami" {
  name = "/shifter/ami/ubuntu"
}

data "aws_ssm_parameter" "windows_ami" {
  name = "/shifter/ami/windows"
}

data "aws_ssm_parameter" "dc_ami" {
  name = "/shifter/ami/dc"
}

data "aws_caller_identity" "current" {}
