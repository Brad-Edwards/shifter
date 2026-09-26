# AWS tenant standup gotchas

Part of the Shifter deploy and operations docs; start at the [documentation home](../index.md).

A running log of operator gotchas hit while standing up or re-pointing an AWS
Shifter tenant that are **not** fixed in code, only in operator procedure. Fixes
that belong in the scripts, workflows, or Terraform go into the code, not here.
Use this alongside the authoritative runbooks:
[`deploy-secrets.md`](deploy-secrets.md),
[`aws-terraform-apply-order.md`](aws-terraform-apply-order.md),
[`aws-runner-provisioning-runbook.md`](aws-runner-provisioning-runbook.md),
[`aws-ami-seeding-runbook.md`](aws-ami-seeding-runbook.md), and
[`scripts/bootstrap/README.md`](https://github.com/Brad-Edwards/shifter/blob/dev/scripts/bootstrap/README.md).

## Re-pointing an existing tenant at a new account ("moved account")

When an environment (for example `aws-dev`) is moved to a different AWS account,
its GitHub Actions **repository-level** `_<ENV>` secrets and variables still point
at the old account. Nothing repoints them automatically; only two of them are
bootstrap-owned. Repoint the rest by hand before the first deploy, or the deploy
runs against a mix of the new role and old-account resource IDs.

Bootstrap-owned (rewritten by `deploy.py bootstrap --env <env>`):

- `AWS_ROLE_ARN_<ENV>`
- `TF_INFRA_STATE_BUCKET_<ENV>`

Operator-owned, account-specific — must be regenerated/repointed for the new
account:

- `TF_VARS_<ENV>_CORE`, `TF_VARS_<ENV>_PORTAL`, `TF_VARS_<ENV>_RANGE` — carry
  account-suffixed bucket names, domains, alarm email, `vm_series_ami_id`, etc.
  Regenerate from local `local.auto.tfvars` overlays with
  `scripts/sync-deploy-secrets.sh --env <env>`.
- `SHIFTER_CONFIG_<ENV>_RANGE` — the deployment `shifter.yaml`.
- `AWS_IMAGE_ROLE_ARN_<ENV>` — from the new account's
  `platform/terraform/global/iam` output `github_actions_image_role_arn`.
- Repository **variables** `PACKER_BUILD_{VPC,SUBNET}_ID_<ENV>` and
  `PACKER_VERIFY_{SUBNET,SG,INSTANCE_PROFILE}_<ENV>` — VPC/subnet/SG IDs are
  account-specific. The verify instance profile name is deterministic
  (`shifter-<env>-range-range-instance`) but must exist in the new account after
  the range apply.

Tell the moved account apart from a fresh one by comparing a known variable
(for example `PACKER_BUILD_VPC_ID_<ENV>`) against the target account's actual VPC
IDs; a mismatch means the value still points at the old account.

## Non-TTY / headless bootstrap needs `--yes`

`deploy.py bootstrap|terraform|full` run without a terminal (an automation
wrapper, CI, an agent) must pass `--yes`. Without it the routine proceed prompts
fall to their non-interactive fallback and the GitHub-secret and backend-config
steps are skipped. `--yes` never authorizes the `account-recovery` sweep, which
always needs its own `--sweep`.
