# Bootstrap Scripts

Bootstrap automation for Shifter infrastructure.

## Features

The `deploy.py` CLI provides an interactive walkthrough for bootstrapping a bare AWS account and deploying infrastructure with intelligent automation:

**Automated Steps (with confirmation):**
- GitHub secrets configuration (via `gh` CLI)
- Per-environment `.s3.tfbackend` file updates
- Git commit and push

**Manual Steps (external systems):**
- DNS record creation (ACM validation, ALB pointing)

**AWS Bootstrap Creates:**
- S3 bucket for Terraform state (with `use_lockfile = true` S3 native locking — no DynamoDB)
- GitHub OIDC provider for keyless CI/CD
- IAM role with all required permissions
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

## Fresh Proof Account Order

Proof is a separate AWS tenant copied from the current `aws-dev` infrastructure
shape. Treat it as its own environment: do not run proof bootstrap with
`--env dev`, because that would rewrite dev backend files and the
`AWS_ROLE_ARN_DEV` secret.

1. Run `bootstrap --env proof --profile proof` to create the proof state
   bucket, GitHub OIDC provider, and deploy role. Let it update
   `AWS_ROLE_ARN_PROOF` and the proof `.s3.tfbackend` files.
2. If proof needs its own self-hosted runner pool, update
   `platform/terraform/global/github-runner/proof.tfvars`, apply the runner
   root with `proof.s3.tfbackend`, and register the runners. Existing
   self-hosted runners can also deploy proof by assuming `AWS_ROLE_ARN_PROOF`.
3. Seed or build the `/shifter/ami/{kali,ubuntu,windows,dc}` SSM parameters in
   the proof account. The Packer workflow accepts `environment=proof`; the Kali
   build still requires the target account to accept the free AWS Marketplace
   terms for product code `7lgvy7mt78lgoi4lant0znp5h`.
4. Configure `TF_VARS_PROOF_PORTAL` in GitHub Actions with the proof portal
   `local.auto.tfvars` payload.
5. For the first proof deploy, run the `Deploy` workflow manually on
   `aws-proof`. Manual dispatch forces the AWS chain (Core -> Range -> Engine
   -> Platform). After the first full run succeeds, normal filtered
   `aws-proof` pushes are appropriate.
6. During the first platform apply, publish the ACM and SES validation records,
   then publish runtime DNS records for `proof.shifter.keplerops.com`,
   `chat.proof.shifter.keplerops.com`, and `proof-polaris.keplerops.com`.

### Bootstrap Only
```bash
./scripts/bootstrap/deploy.py bootstrap --env proof --profile proof
```

### Terraform Only (after bootstrap)
```bash
./scripts/bootstrap/deploy.py terraform --env prod --profile <your-prod-profile>
```

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

- `--env` (required): `dev`, `proof`, or `prod`
- `--profile` (required): AWS CLI profile name
- `--dry-run` (optional): Show what would happen without making changes
- `--project-id` (GDC only): GCP project ID, defaults to `PANW_GCP_DEV` or repo-root `.env`
- `--cluster-id` (GDC only): Cluster name / asset prefix, defaults to `cluster1`
- `--google-account-email` (GDC only): Optional Google identity to grant cluster-admin in cluster YAML

## Help

```bash
./scripts/bootstrap/deploy.py --help
./scripts/bootstrap/deploy.py bootstrap --help
./scripts/bootstrap/deploy.py terraform --help
./scripts/bootstrap/deploy.py full --help
./scripts/bootstrap/deploy.py gdc-bootstrap --help
```
