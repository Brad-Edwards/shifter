# AWS EKS backend bundle

The AWS backend uses EKS and `platform/charts/shifter` as the canonical
packaging for Shifter platform workloads. AWS range tasks remain private ECS
tasks behind the existing ADR-039 range adapter; Cognito remains the user
identity provider. These are separate boundaries.

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
rejected. The lifecycle owner validates root config, runs shared preflight,
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
  launcher is disabled for AWS because range delivery remains ECS.

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
