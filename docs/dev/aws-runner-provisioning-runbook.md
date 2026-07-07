# AWS self-hosted runner provisioning

Every AWS deploy job (`_core.yml`, `_range.yml`, `_shifter-engine.yml`,
`_shifter-platform.yml`, `packer.yml`) runs on `runs-on: self-hosted`. A fresh
AWS account has no runners, so the AWS Deploy workflow cannot run until you
provision and register at least one. Bootstrap does not create runners; it
creates the shared state backend the runner Terraform root reuses.

This runbook is the standup entry point. The authoritative reference for runner
architecture, health monitoring, removal, and gotchas is
[`platform/terraform/global/github-runner/README.md`](../../platform/terraform/global/github-runner/README.md).

## Prerequisite

Run `scripts/bootstrap/deploy.py bootstrap --env <env>` first. It creates the S3
state bucket and rewrites `platform/terraform/global/github-runner/<env>.s3.tfbackend`
so the runner root uses the same backend.

## 1. Choose the runner network

The runner subnet must be non-default and outside any VPC where range
provisioning can create private-DNS interface endpoints. Use a dedicated runner
VPC or the portal VPC private tier; the account default VPC is not acceptable.
The subnet needs outbound egress for GitHub, ECR, SSM, and AWS APIs. See the
network isolation preflight:
[`docs/architecture/github-runner-network-isolation-preflight-1222.md`](../architecture/github-runner-network-isolation-preflight-1222.md).

If you use the portal VPC, read the IDs from the portal Terraform outputs:

```bash
cd platform/terraform/environments/<env>/portal
terraform output vpc_id
terraform output private_subnet_ids
```

Do not commit live VPC or subnet IDs to the placeholder `dev.tfvars` /
`proof.tfvars`. Keep them in a gitignored operator override or another approved
deploy-time binding.

## 2. Terraform inputs

Defined in `platform/terraform/global/github-runner/variables.tf`:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `vpc_id` | yes | none | Runner VPC (dedicated or portal private tier). |
| `subnet_id` | yes | none | Non-default subnet with GitHub/ECR/SSM/AWS egress. |
| `runner_count` | no | `2` | `dev.tfvars` / `proof.tfvars` set `3`. |
| `instance_type` | no | `t3.large` | Amazon Linux 2023, SSM access, no inbound. |
| `region` | no | `us-east-2` | |
| `github_org` / `github_repo` | no | `Brad-Edwards` / `shifter` | |
| `alarm_email` | no | empty | Subscribe an inbox to the runner-alerts SNS topic. |
| `enable_system_auto_recovery` | no | `true` | EC2 auto-recover on system status-check failure. |
| `cpu_alarm_threshold` | no | `95` | Sustained-CPU hang-proxy alarm. |

Sizing: start with `runner_count = 3` at `t3.large`. That covers the parallel
`Core` / `Range` / `Engine` / `Platform` jobs of one deploy with headroom; raise
`runner_count` if deploys queue on runner availability.

## 3. Apply the runner root

```bash
export AWS_PAGER=""
./scripts/runner-deploy.sh              # init + plan
./scripts/runner-deploy.sh --apply      # init + apply
```

The script reads `PANW_SHIFTER_DEV_PROFILE`. A fresh apply leaves each host
running the runner service but unregistered (`RunnerServiceActive = 0`) until
step 4.

## 4. Register each runner

Registration needs a single-use registration token from GitHub, exchanged once
for long-lived credentials on the instance. Mint one token per runner:

```bash
export AWS_PROFILE=aws-dev
export AWS_PAGER=""

INSTANCE=i-xxxxxxxxxxxxxxxxx
NAME=shifter-github-runner-N
TOKEN=$(gh api -X POST /repos/Brad-Edwards/shifter/actions/runners/registration-token --jq .token)

aws ssm send-command \
  --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript \
  --region us-east-2 \
  --parameters "commands=[
    \"set -ex\",
    \"cd /home/ec2-user/actions-runner\",
    \"sudo -u ec2-user ./config.sh --url https://github.com/Brad-Edwards/shifter --token $TOKEN --labels self-hosted,linux,X64 --unattended --replace --name $NAME\",
    \"./svc.sh install ec2-user\",
    \"./svc.sh start\"
  ]"
```

Registration tokens are single-use and expire in about an hour; mint a fresh one
per runner. Verify the fleet is online:

```bash
gh api repos/Brad-Edwards/shifter/actions/runners --jq '.runners[] | {name, status}'
```

Once every runner shows `status: online`, the AWS Deploy workflow can run. To
remove or replace a runner, see the "Removing a runner" section of the
[github-runner README](../../platform/terraform/global/github-runner/README.md).
