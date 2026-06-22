# Fill in your target account's VPC/subnet IDs before applying the runner root.
# See docs/dev/deploy-secrets.md ("Fresh AWS account bootstrap order", step 2).
vpc_id       = "vpc-xxxxxxxxxxxxxxxxx"    # Default VPC in the target account
subnet_id    = "subnet-xxxxxxxxxxxxxxxxx" # public subnet (e.g. us-east-2a)
runner_count = 3

github_org  = "Brad-Edwards"
github_repo = "shifter"

# Access via SSM Session Manager - no SSH required
