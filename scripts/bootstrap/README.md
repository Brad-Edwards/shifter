# Bootstrap Scripts

Bootstrap automation for Shifter infrastructure.

## Features

The `deploy.py` CLI provides an interactive walkthrough for bootstrapping a bare AWS or GCP account and deploying infrastructure with intelligent automation:

**Automated Steps (with confirmation):**
- GitHub secrets configuration (via `gh` CLI)
- Per-environment `.s3.tfbackend` file updates
- Git commit and push

**Manual Steps (external systems):**
- DNS record creation (ACM validation, ALB pointing)

**AWS Bootstrap Creates:**
- S3 bucket for Terraform state (with `use_lockfile = true` S3 native locking, no DynamoDB)
- GitHub OIDC provider for keyless CI/CD
- IAM role with all required permissions. The role uses an inline
  AdministratorAccess-equivalent policy so bootstrap works in AWS
  organizations that deny `iam:AttachRolePolicy` via SCP.
- Optionally deploys Terraform infrastructure

**GDC Bootstrap Creates:**
- required GDC/GKE/GCP APIs and IAM bindings
- the custom VPC/subnet/firewall substrate for the eval cluster
- the Compute Engine admin workstation and cluster nodes
- the rendered `bmctl` cluster config and bootstrap bundle
- the hybrid GDC cluster plus VM Runtime enablement
- the inotify hardening needed to keep `macvtap-deviceplugin` stable
- admin workstation helpers for repeatable kubeconfig access
- the `shifter-gcp-dev-gdc-access` Secret Manager bundle consumed by the provisioner for GDC range-plane access

## Interactive Prompts

When automated options are available, you'll see:
```
[y/n/m]:
  y = yes (run automatically)
  n = no (abort - all steps are required)
  m = manual (show instructions and wait)
```

**Note:** All steps are mandatory for a functioning deployment. Choosing 'n' will abort the script with an explanation of why that step is required.

## Commands

## Fresh AWS Account Order

For a new AWS account, run bootstrap-only first. Do not start with `full`.
The self-hosted runner Terraform root uses the same S3 backend that
bootstrap creates, and the AWS deploy workflows cannot run until the
runners are provisioned and registered.

1. Run `bootstrap --env dev --profile <profile>` to create the shared dev
   state bucket, GitHub OIDC provider, and deploy role. Let it set the
   `AWS_ROLE_ARN_DEV` and `TF_INFRA_STATE_BUCKET_DEV` GitHub secrets (the
   env-suffixed names the dev deploy workflows read; prod uses the unsuffixed
   `AWS_ROLE_ARN` / `TF_INFRA_STATE_BUCKET`) and update the dev `.s3.tfbackend`
   files.
2. Run `runners --env dev --profile <profile>` to provision the runner fleet
   **and** register it automatically (issue #1433). By default it provisions a
   dedicated, ADR-004-R20-compliant runner VPC (`create_runner_network`), applies
   `platform/terraform/global/github-runner`, mints a single-use token per runner,
   registers each over SSM, and verifies it online, with no manual `config.sh`. Pass
   `--use-existing-network` to reuse an operator-supplied `vpc_id`/`subnet_id` or
   the `allow_default_vpc` opt-in instead of creating a VPC. Registration tokens
   are never written to Terraform state, user data, a secret store, or logs.
3. Confirm the fleet is online (the `runners` path already verifies this):
   `gh api repos/Brad-Edwards/shifter/actions/runners --jq '.runners[] | {name, status}'`.
4. Seed or build the `/shifter/ami/{kali,ubuntu,windows,dc}` SSM
   parameters required by portal Terraform. The Kali build requires the target
   account to accept the free AWS Marketplace terms for product code
   `7lgvy7mt78lgoi4lant0znp5h`.
5. Configure the deployment tfvars secrets documented in
   `docs/dev/deploy-secrets.md`, including `TF_VARS_DEV_PORTAL` and
   `TF_VARS_DEV_RANGE`. Bootstrap configures the AWS role secret and backend
   files; deploy-time portal/range values live in those tfvars secrets for
   GitHub Actions, or in gitignored `local.auto.tfvars` files for local
   Terraform runs.
6. For the first deploy in the moved account, run the `Deploy` GitHub Actions
   workflow manually with `workflow_dispatch` on `aws-dev`. Manual dispatch
   forces the full AWS chain (Core -> Range -> Engine -> Platform). A plain
   branch push still obeys path filters, so it can skip Core or image
   publishing when the pushed commit only touched bootstrap/backend files.
   After the first full run succeeds, normal filtered `aws-dev` pushes are
   appropriate.
7. During that first platform apply, publish DNS records for ACM and SES
   validation in the authoritative DNS zone. ACM records come from the root
   Terraform output `acm_validation_records`. SES records come from
   `aws ses get-identity-verification-attributes` for the `_amazonses` TXT
   value and `aws ses get-identity-dkim-attributes` for the three DKIM CNAME
   tokens. In Cloudflare, keep ACM and DKIM CNAMEs DNS-only.
8. After platform apply creates runtime endpoints, publish routing records:
   `domain_name` and `chat.<domain_name>` CNAME to the root Terraform output
   `alb_dns_name`; `ctfd_domain` A-records to `ctfd_elastic_ip`.

The portal VPC creates private AWS service endpoints for the bootstrap-critical
services used by EC2 user_data and Fargate task startup: ECR, S3, CloudWatch
Logs, Secrets Manager, SSM, STS/KMS, ECS/EC2/ELB, SNS, SQS, and DynamoDB.
These endpoints are expected in fresh accounts, especially when portal
inspection is enabled, because the private default route traverses the
firewall/NAT path while Docker install, image pulls, ECS secret resolution, and
awslogs setup are all first-boot or task-initialization work.

## Fresh GCP Account Order

GCP standup mirrors the AWS order: prepare identity and images first, bootstrap
the substrate, then deploy. The maintained end-to-end walkthrough is the GCP
Deployment section of
`shifter/shifter_platform/documentation/docs/technical/dev/setup.md`.

1. Create the GCP project and enable the required APIs.
2. Configure Workload Identity Federation for GitHub Actions (pool, provider,
   service account) and set the `GCP_SERVICE_ACCOUNT` and
   `GCP_WORKLOAD_IDENTITY_PROVIDER` GitHub secrets.
3. Configure the GCP deployment secrets and variables in
   `docs/dev/deploy-secrets.md` (the `gcp-dev` section), including
   `SHIFTER_CONFIG_GCP_DEV` and the GCE range-cell variables.
4. Build the range guest images and set the image variables before deploy. The
   GCP range backend defaults to the GCE range-cell path, and a range launch
   needs the guest images to exist. See `docs/dev/gcp-range-cell-deploy.md` and
   `docs/architecture/gcp-guest-images.md`.
5. Bootstrap the GDC/GKE substrate and control plane with `gdc-bootstrap` (see
   the command below). It applies the GCP Terraform (GKE, Cloud SQL,
   Memorystore, Pub/Sub), builds and pushes the control-plane images, renders
   Helm values from Terraform outputs and Secret Manager, and installs the
   Shifter Helm release.
6. Subsequent deploys run through CI: push to the `gcp-dev` branch, the only
   branch that deploys the GCP dev environment (see the CI/CD trigger matrix in
   `shifter/shifter_platform/documentation/docs/technical/dev/ci-cd.md`).
7. Point the configured hostname at the reserved global ingress IP so the
   Google-managed TLS certificate can activate.

### Preflight (validate prerequisites, no changes)
```bash
./scripts/bootstrap/deploy.py preflight --cloud aws --env dev
./scripts/bootstrap/deploy.py preflight --cloud aws --env dev --component portal
./scripts/bootstrap/deploy.py preflight --cloud gcp --env gcp-dev
# --headless : non-interactive; fail on any missing required prerequisite, no prompts
```
The shared, fail-safe prerequisite gate (`preflight.py`): checks tools, secrets,
and config and reports every gap up front before any change. The same checks run
in the CI deploy workflows. `bootstrap`, `terraform`, and `full` run it
automatically at the start; it is interactive by default (auto-headless off a
TTY). The first Identity Platform operator credentials are required unless
`SHIFTER_SKIP_OPERATOR_BOOTSTRAP=true` is set (the skip is logged, never silent).
See `docs/dev/deploy-secrets.md`.

### Bootstrap Only
```bash
./scripts/bootstrap/deploy.py bootstrap --env prod --profile <your-prod-profile>
```

### Terraform Only (after bootstrap)
```bash
./scripts/bootstrap/deploy.py terraform --env prod --profile <your-prod-profile>
```

### Runners (provision + auto-register self-hosted runners)
```bash
./scripts/bootstrap/deploy.py runners --env dev --profile <your-dev-profile>
# --use-existing-network : reuse a configured vpc_id/subnet_id or allow_default_vpc opt-in
# --runner-count N       : override runner_count for this apply
# --dry-run              : show the plan without minting a token or sending SSM commands
```
Provisions the runner fleet (dedicated runner VPC by default) and registers each
runner end-to-end. Registration tokens are minted per runner and never persisted
to Terraform state, user data, a secret store, or logs (issue #1433).

### Full Deployment (bootstrap + terraform)
```bash
./scripts/bootstrap/deploy.py full --env prod --profile <your-prod-profile>
```

### Dry Run (preview without changes)
```bash
./scripts/bootstrap/deploy.py full --env prod --profile <your-prod-profile> --dry-run
```

### Bootstrap a Repeatable GDC VM Runtime Cluster
```bash
./scripts/bootstrap/deploy.py gdc-bootstrap --project-id prod-rwctxzl6shxk --cluster-id cluster1
```

This follows the official Google Distributed Cloud on Compute Engine evaluation path, but bakes in the
repo-specific fixes from the live spike:
- custom VPC/subnet instead of relying on `default`
- `multipleNetworkInterfaces: true` in the cluster config
- a deterministic `vxlan0` underlay for later shared-L2 scenario networks
- the persistent inotify sysctl fix before VM Runtime workloads are enabled

## Options

- `--env` (required): `dev`, `proof`, or `prod` (`gcp-dev` for the `preflight --cloud gcp` path)
- `--profile` (required): AWS CLI profile name
- `--dry-run` (optional): Show what would happen without making changes
- `--cloud` (preflight only): `aws` or `gcp`
- `--component` (preflight only): `core`, `range`, or `portal` to scope AWS overlay checks
- `--headless` (bootstrap/terraform/full/preflight): non-interactive; fail on missing prerequisites without prompting (auto-detected off a TTY)
- `--use-existing-network` (runners only): Reuse a configured `vpc_id`/`subnet_id` or the `allow_default_vpc` opt-in instead of provisioning a dedicated runner VPC
- `--runner-count` (runners only): Override `runner_count` for this apply
- `--project-id` (GDC only): GCP project ID, defaults to `PANW_GCP_DEV` or repo-root `.env`
- `--cluster-id` (GDC only): Cluster name / asset prefix, defaults to `cluster1`
- `--google-account-email` (GDC only): Optional Google identity to grant cluster-admin in cluster YAML

## Help

```bash
./scripts/bootstrap/deploy.py --help
./scripts/bootstrap/deploy.py preflight --help
./scripts/bootstrap/deploy.py bootstrap --help
./scripts/bootstrap/deploy.py terraform --help
./scripts/bootstrap/deploy.py runners --help
./scripts/bootstrap/deploy.py full --help
./scripts/bootstrap/deploy.py gdc-bootstrap --help
```
