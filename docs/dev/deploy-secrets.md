# Deploy Secrets

AWS deploy workflows select deployment secrets by `environment` and fail when
the active environment's secret is empty. Keep tenant values separate so proof
cannot fall back to aws-dev.

## AWS role secrets

| Environment | Secret |
| --- | --- |
| `dev` | `AWS_ROLE_ARN_DEV` |
| `proof` | `AWS_ROLE_ARN_PROOF` |
| `prod` | `AWS_ROLE_ARN` |

## Portal tfvars secrets

The platform workflow writes the active secret body to
`platform/terraform/environments/<env>/portal/local.auto.tfvars` before
Terraform plan/apply.

| Environment | Secret |
| --- | --- |
| `dev` | `TF_VARS_DEV_PORTAL` |
| `proof` | `TF_VARS_PROOF_PORTAL` |
| `prod` | `TF_VARS_PROD_PORTAL` |

`TF_VARS_PROOF_PORTAL` should contain the proof-specific overrides copied from
the proof portal root, including `domain_name`, `ctfd_domain`, `ses_domain`,
`ctf_from_email`, `alarm_email`, `allowed_email_domains`, CTFd SSH settings,
and `user_storage_bucket`.

## Proof bootstrap order

1. Run `./scripts/bootstrap/deploy.py bootstrap --env proof --profile proof`.
2. Seed `/shifter/ami/{kali,ubuntu,windows,dc}` in the proof account. The
   Packer workflow supports `environment=proof`.
3. Configure `TF_VARS_PROOF_PORTAL`.
4. Dispatch `Deploy` on branch `aws-proof` for the first full proof deploy.
