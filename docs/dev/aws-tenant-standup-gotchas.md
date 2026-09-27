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

## Standalone `runners` needs `TF_INFRA_STATE_BUCKET` exported

`deploy.py runners --env <env> --profile <p>` (the standalone path, not `full`)
does not thread the bootstrap state bucket, so it resolves it from the
`TF_INFRA_STATE_BUCKET` environment variable. Export it to the bootstrap bucket
before running, or the runner apply fails closed with `Set TF_INFRA_STATE_BUCKET
or pass the bootstrap bucket name`. (The `full` path threads it automatically.)

## First-deploy AMI ordering (Core → Range → seed AMIs → Portal)

A single `deploy.yml` dispatch runs the full chain, but Portal reads
`/shifter/ami/*` as data sources and the packer fresh-boot verify gate
(`PACKER_VERIFY_*`) needs the range instance profile + a range-VPC subnet/SG,
which only exist after the Range stack applies. So a fresh account cannot
complete Portal on the first dispatch. The order is: dispatch once (Core + Range
apply; Portal fails on missing AMIs), set `PACKER_VERIFY_*` from the applied
range, seed `/shifter/ami/*` via `packer.yml`, then dispatch again to complete
Portal.

## `PACKER_VERIFY_*` values and the verify security group

After the Range stack applies, set the fresh-boot verify-gate variables from it:

- `PACKER_VERIFY_INSTANCE_PROFILE_<ENV>` = range output
  `range_instance_profile_name` (`shifter-<env>-range-range-instance`).
- `PACKER_VERIFY_SUBNET_ID_<ENV>` = the range VPC's `ssm-endpoints` subnet (or
  any range-VPC subnet; the ssm/ssmmessages/ec2messages interface endpoints have
  private DNS and are reached VPC-locally, and the endpoint SG allows 443 from the
  whole VPC CIDR).
- `PACKER_VERIFY_SG_<ENV>` = a **no-inbound, egress-all** security group. The
  range stack does **not** provide one and locks the VPC default SG down to no
  egress, so create a dedicated SG in the range VPC (tag it
  `Project=shifter,Purpose=packer-verify`). This is operational AMI-pipeline
  infra, not range Terraform, so it does not appear in the range plan.

## No AWS pre-promoted DC bake (`/shifter/ami/dc`)

`/shifter/ami/dc` must be a **pre-promoted** `internal.shifter` DC:
`_should_promote_dc_at_runtime()` is hard-disabled, so runtime promotion never
happens. AWS `dc.pkr.hcl` builds only a *generalized* Windows image (AD DS
installed, not promoted) and runs `sysprep` (which is incompatible with a
promoted DC), and the runbook forbids publishing it as `/shifter/ami/dc`. The
pre-promoted bake (`a2_setup.ps1`) exists only on the GCP side; the AWS DC AMIs
in `shifter/packer/dc-amis.json` were created manually and are account-scoped, so
they are not usable in a moved/new account. A new account therefore has no valid
DC AMI. Real fix: implement a pre-promoted DC bake for AWS (port GCP's
`a2_setup` promotion, drop sysprep) or manually promote+capture one, then update
`dc-amis.json` / seed `/shifter/ami/dc`. To unblock a base-range standup (which
never launches a DC) meanwhile, seed `/shifter/ami/dc` with a placeholder AMI so
the Portal plan's data source resolves; repoint it once a real DC is baked.

## Debugging apply time Terraform bugs

The deploy workflow re-runs the ~25-min Quality gate on every dispatch, so
iterating apply time Terraform errors through CI is slow. For the debug/converge
phase, run the identical `terraform apply` locally against the same S3 backend
and rendered overlays (what `_range.yml` / `deploy.py terraform` run), fix each
error, then validate the full chain with one CI `deploy.yml` dispatch.
