// Proof environment Packer variables

aws_region    = "us-east-2"
instance_type = "t3.large"
ami_prefix    = "shifter"
// Empty vpc_id/subnet_id => the packer templates fall back to null, so the
// amazon-ebs builder auto-selects the account's default VPC + a public subnet
// (see kali/ubuntu/windows/dc .pkr.hcl: `var.x != "" ? var.x : null`). Proof
// builds run in the default VPC where the self-hosted runners live.
vpc_id        = ""
subnet_id     = ""
