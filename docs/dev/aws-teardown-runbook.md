# AWS environment teardown

This is the runbook for tearing an AWS environment down to zero: the Terraform
stacks, the resources that block `terraform destroy`, the bootstrap-created
identity and state backend, and the local operator config. It is the reverse of
[`aws-terraform-apply-order.md`](aws-terraform-apply-order.md).

There is no `aws-<env>-destroy.yml` workflow yet (the GCP path has
`gcp-dev-destroy.yml`). Building a repeatable AWS destroy workflow is tracked in
issue #1287; it will be authored from this runbook once the manual sequence is
validated live, so the workflow encodes a proven order rather than a guess. Until
then, teardown is the manual sequence below.

> **Destroys real infrastructure.** Run only against the intended environment.
> Confirm the active AWS profile and account id before every destructive step:
> `aws sts get-caller-identity`.

## Destroy order

Destroy stacks in reverse dependency order: **Portal, then Range, then Core.**
The Portal stack reads Core and Range remote state, so it must go first. Each
stack initializes with its own `-backend-config=<env>.s3.tfbackend`.

## 1. Lift deletion protection (prod, and any env that enabled it)

Several resources ship deletion protection on (secure default in prod; `false`
in dev/proof, so dev/proof usually need no change). For any environment where
these are `true`, set the tfvars to `false` and `terraform apply` the owning
stack first, so the live resource drops protection before destroy:

| Resource | tfvars input | Stack |
|---|---|---|
| Portal RDS | `db_deletion_protection` | Portal |
| Guacamole RDS | `guacamole_db_deletion_protection` | Portal |
| Portal ALB | `enable_deletion_protection` (prod hardcoded `true`) | Portal |
| Portal inspection Network Firewall | `portal_inspection_delete_protection` | Portal |
| Range egress Network Firewall | `network_firewall_delete_protection` | Range |
| Cognito user pool | `deletion_protection` (`ACTIVE`/`INACTIVE`) | Portal |

The prod portal ALB protection is a hardcoded `true` literal
(`environments/prod/portal/main.tf`); flip it to `false` and apply before
destroy.

## 2. Empty S3 buckets and ECR repos

No S3 bucket sets `force_destroy` and no ECR repo sets `force_delete`, so a
non-empty bucket or repo blocks destroy. Empty these first.

S3 buckets (per stack):

- Portal user-storage bucket (Portal).
- Log-aggregation `logs` and `alb_access_logs` buckets (Portal).
- Engine state bucket (Portal, `engine-state` module; `force_destroy = false`).

ECR repos (Core stack, four repos; prod drops the `<env>-`):
`shifter-<env>-portal`, `shifter-<env>-pulumi-provisioner`,
`shifter-<env>-guacd`, `shifter-<env>-guacamole-client`.

Empty a versioned S3 bucket (delete all object versions and delete markers),
then empty each ECR repo:

```bash
# S3: delete all versions + markers, then the bucket is destroyable by Terraform.
aws s3api list-object-versions --bucket "$BUCKET" \
  --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json \
  > /tmp/versions.json
aws s3api delete-objects --bucket "$BUCKET" --delete file:///tmp/versions.json
# Repeat for DeleteMarkers.

# ECR: delete all images in a repo.
aws ecr batch-delete-image --repository-name "$REPO" \
  --image-ids "$(aws ecr list-images --repository-name "$REPO" \
    --query 'imageIds[*]' --output json)"
```

## 3. Destroy the stacks

For each stack, in order Portal, Range, Core:

```bash
cd platform/terraform/environments/<env>/<stack>
terraform init -backend-config=<env>.s3.tfbackend
terraform destroy
```

If a destroy fails on an unremovable resource (for example a KMS key with
`prevent_destroy`), remove it from state with `terraform state rm` and let the
account-level cleanup handle it, mirroring the KMS handling in
`gcp-dev-destroy.yml`.

## 4. Destroy the runner root and deregister runners

```bash
# Deregister each runner from GitHub first (from the EC2 via SSM):
#   cd /home/ec2-user/actions-runner
#   TOKEN=$(gh api -X POST /repos/Brad-Edwards/shifter/actions/runners/remove-token --jq .token)
#   sudo ./svc.sh stop && sudo ./svc.sh uninstall
#   sudo -u ec2-user ./config.sh remove --token "$TOKEN"
./scripts/runner-deploy.sh --destroy
```

See [`aws-runner-provisioning-runbook.md`](aws-runner-provisioning-runbook.md).

## 5. Remove bootstrap-created identity and backend

These are created by bootstrap, not by the env stacks, so `terraform destroy`
does not remove them:

- **GitHub OIDC provider**
  `arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com`.
- **Deploy role** `github-actions-shifter-<env>` and any stray temporary
  `github-actions-shifter-<env>-bootstrap` role.
- **The `{uuid}` state bucket** (`shifter-<env>-infra-<uuid>` for dev/proof,
  `shifter-infra-<uuid>` for prod). It is versioned; delete all versions, then
  the bucket. Do this last, after all stacks are destroyed, because it holds
  their state.

```bash
aws iam delete-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN"
# Delete role inline policies / instance profiles as needed, then:
aws iam delete-role --role-name "github-actions-shifter-<env>"
```

## 6. Clear local operator config

```bash
rm -rf ~/.shifter/<env>-<bucket>/
```

## 7. Delete the GitHub environment and its secrets

Delete the environment-scoped and per-env deploy secrets for the environment
being retired (for example `AWS_ROLE_ARN_DEV`, `TF_INFRA_STATE_BUCKET_DEV`,
`TF_VARS_DEV_*`, `SHIFTER_CONFIG_DEV_RANGE`, `SMOKE_*`). Keep shared/prod
secrets (`AWS_ROLE_ARN`, `TF_INFRA_STATE_BUCKET`, `TF_VARS_PROD_PORTAL`,
`SONAR_TOKEN`, `PLATFORM_BOOTSTRAP_STAFF_EMAILS`,
`PLATFORM_BOOTSTRAP_SUPERUSER_EMAILS`). See
[`deploy-secrets.md`](deploy-secrets.md) for the full list.

## 8. Verify the account is empty

Confirm no residual EC2, ASG, RDS, ALB, Network Firewall, ECR, IAM
`github-actions-shifter-*` roles, OIDC provider, or `{uuid}` state bucket
remain before a fresh bootstrap.
