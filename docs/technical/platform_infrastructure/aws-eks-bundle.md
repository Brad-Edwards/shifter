# AWS EKS backend bundle

The AWS backend uses EKS and `platform/charts/shifter` as the canonical
packaging for Shifter platform workloads, and the provisioner dispatches as a
Kubernetes Job on the cluster (mirroring GCP). Range and target delivery (the
VMs inside a range) remain ECS/VM behind the existing ADR-039 range adapter;
Cognito remains the user identity provider. These are separate boundaries.

## Authoritative inputs and owners

The explicit entrypoint is:

```console
./scripts/bootstrap/deploy.py eks-deploy \
  --config shifter.yaml \
  --images .shifter/images.json \
  --profile operator
```

`shifter.yaml` must select `backend: aws`. The image file is a JSON mapping
whose values are attested `repository@sha256:<digest>` identities. Tags are
rejected. Protected input files must resolve beneath the repository, the system
temporary directory, `RUNNER_TEMP`, or an operator-selected
`SHIFTER_PROTECTED_INPUT_ROOT`; symbolic links are rejected.
The lifecycle owner validates root config, runs shared preflight,
applies a saved plan from the isolated
`platform/terraform/environments/<profile>/eks` root, acquires cluster access
through the Terraform-output deploy role, and performs an atomic Helm upgrade.

Terraform owns the cluster, private placement, workload identity roles,
certificate/WAF/DNS prerequisites, KMS, and secret stores. Helm owns namespaced
platform workloads. ECS/ASG portal state and range task state are outside the
EKS root and are never imported, adopted, renamed, or destroyed by it.

## Security boundaries

- GitHub OIDC authorizes the protected deployment runner.
- EKS workload identity binds exact namespace/service-account subjects to
  workload roles; pods do not inherit node-wide credentials.
- Cognito/OIDC authenticates users and is not cluster authentication.
- Provider values carry public settings and secret references only. Runtime
  values are hydrated from Secrets Manager by the existing entrypoint boundary;
  they do not enter Helm values, ConfigMaps, argv, or committed files.
- The shared chart renders restricted, non-root, read-only workloads with
  bounded resources and default-deny network policy. The Kubernetes Job
  launcher is enabled for AWS: the provisioner dispatches as a fail-closed,
  admission-gated Kubernetes Job (ADR-044-R6) using exact-subject IRSA for the
  launcher and provisioner service accounts, the dedicated-launcher RBAC, and
  restricted Pod Security. Range and target delivery remain ECS/VM behind the
  ADR-039 range adapter.

## Provisioner environment sourcing (ADR-044-R6)

The provisioner Job's environment is composed over the existing portal and
range data plane rather than owned by the EKS bundle. The range stack publishes
its topology (VPC/subnet/route-table/security-group IDs, endpoint IDs, ARNs) to
`/shifter/<env>/range/*` in SSM Parameter Store, since those opaque IDs are not
discoverable by a native `data.aws_*` lookup. The `eks-provisioner-env`
Terraform module reads that contract, reads the shared portal RDS/secrets
KMS/agent bucket/VPC via native AWS data sources, reads the prebaked
`/shifter/ami/*` pointers, and merges the result with the deploy-tooling
management-plane runtime env. `terraform_remote_state` is never used
(enforced by the `eks-cross-stack-sourcing` guard); secret payloads flow only as
references through the existing hydration boundary.

## Teardown and evidence

```console
./scripts/bootstrap/deploy.py eks-teardown \
  --config shifter.yaml \
  --profile operator
```

Teardown removes the Helm release, destroys only the isolated EKS root, and
fails if that Terraform state remains non-empty. Deploy succeeds only after the
atomic rollout and HTTPS `/health/` probe succeed.

For transition guidance, follow
[Migrate an AWS deployment from ECS to EKS](../../how-to/aws-ecs-to-eks-migration.md).
