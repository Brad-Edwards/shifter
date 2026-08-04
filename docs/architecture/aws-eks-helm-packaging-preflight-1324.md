# AWS EKS And Backend-Neutral Helm Packaging Preflight

Status: pre-implementation architecture guidance

Date: 2026-07-25

Issue: GitHub #1324, "Canonical Kubernetes/Helm packaging:
backend-neutral chart plus an AWS EKS bundle"

This is a requirement-free run. The GitHub issue title, body, scope, and
acceptance criteria are the shipping contract. This note constrains the design;
it is not an implementation plan.

## Decision Boundary

The Shifter Helm chart becomes the canonical package for control-plane
workloads on both GKE and EKS. Backend bundles continue to own infrastructure,
identity, secret-store integration, generated values, deployment entrypoints,
and health evidence. The chart owns Kubernetes workload shape only.

For a newly configured `backend: aws` deployment, the canonical platform
compute is EKS and the platform is installed through Helm. Do not introduce an
`aws-eks` backend or a second root configuration selector. Existing ECS/ASG
platform deployments remain an explicitly documented compatibility path during
migration; they are not the authoring model for a new root-configured
deployment.

This changes platform workload packaging, not every use of ECS. In particular:

- the existing AWS `TaskRunner` may continue to launch the standalone
  provisioner on private ECS tasks;
- ADR-039's AWS Terraform range-substrate adapter remains the range lifecycle
  implementation; and
- AWS Cognito/OIDC remains the browser identity system.

Moving either task delivery or range convergence into EKS is a separate
runtime/security decision. The EKS chart must not render the GCP Kubernetes Job
launcher, its RBAC, or its admission policy merely because the platform itself
runs on Kubernetes.

ADR-044 records the cross-backend packaging rule. ADR-007 remains the GCP
specialization; ADR-006 remains the Kubernetes workload-security authority;
ADR-011 remains the backend selection and explicit invocation authority.

## Architecture Decisions And Guardrails

### One chart, backend-owned projections

`platform/charts/shifter` is the only canonical workload package. It must have
one backend-neutral workload core and backend-supplied value projections, not
copied `aws` and `gcp` charts.

The neutral chart defaults must not emit GCP resources, annotations, image
roots, load-balancer CIDRs, API VIPs, or ingress classes. Today these leak
through `values.yaml`, `ingress.yaml`, `web-service.yaml`,
`guacamole-client-service.yaml`, the `BackendConfig` templates, and
`networkpolicies.yaml`. Move those decisions to backend values generated from
validated infrastructure outputs.

Provider-specific resources may remain in narrowly gated chart templates where
the provider requires a CRD, but the core render must use Kubernetes-native
objects and render without either provider's CRDs. Do not make templates parse
`CLOUD_PROVIDER` or branch on an installation backend. Values express the
already-selected edge, identity, and network capabilities.

Use Helm's `values.schema.json` as the chart-input shape gate. It validates the
rendered chart projection (names, image references, ingress/TLS settings,
annotations, CIDRs, feature compatibility); it must not duplicate
`RootConfig`, `AwsSettings`, secret-reference grammar, or Terraform output
schemas.

The provider variation seam is:

```text
validated shifter.yaml
  + validated backend Terraform outputs
  + verified OCI image digests
  -> backend-owned renderer
  -> schema-valid non-secret Helm values plus out-of-band Secret resources
  -> one Shifter chart
```

A future backend should add infrastructure plus a renderer/values overlay and
conformance evidence. It should not require a chart fork or provider branches
in templates, Django services, public DTOs, events, or repositories.

### Bundle contract and execution ownership

`BackendBundle`, `GeneratedOutput`, `OwnedFiles`, `ValidationCheck`,
`HealthCheck`, `RequiredTool`, and `CommandSpec` remain the public bundle
vocabulary. Use `GeneratedOutput` with `HELM_VALUE` or `K8S_ARTIFACT` kind for
the non-secret projection and keep provider-owned paths in `OwnedFiles`.

There is a current contract gap: `BackendBundle` has no typed deploy or teardown
entrypoint. Do not encode a mutating command as a `ValidationCheck`, hide it in
`OwnedFiles`, or turn doctor into a deploy runner. If #1324 exposes lifecycle
entrypoints in bundle metadata, add the minimum explicit deploy/teardown
command fields using the existing safe `CommandSpec` rules and retain their
deployment-mutating classification. The command must delegate to the existing
bootstrap/deploy owner, not describe a workflow DSL in registry data.

Any public contract change goes through
`installation/published_contract/MIGRATIONS.md`, deterministic regeneration,
published-schema validation, the compatibility gate, and immutable snapshots.
Never hand-edit the generated JSON or create an AWS-only validator.

`shifter-config doctor` remains non-mutating. The explicit local or workflow
deploy invocation first loads `shifter.yaml` through
`installation.loader.load_root_config`, selects the registered bundle, runs the
shared preflight, and then invokes the backend-owned bootstrap entrypoint. The
registry remains data-only and imports no provider SDK, Terraform driver,
Django code, or callable implementation.

### AWS EKS infrastructure boundary

The EKS Terraform owns cluster and platform prerequisites; the chart owns
namespaced Shifter workloads. Keep those ownership domains explicit:

- Terraform owns VPC/subnets/routes, EKS cluster and node groups, cluster
  access, workload identity roles/associations, load-balancer/controller
  prerequisites, certificates/WAF, provider databases/caches/queues/buckets,
  secret stores/KMS, DNS outputs, and state.
- The chart owns Shifter Deployments, Services, ServiceAccounts, ConfigMaps,
  narrowly required RBAC/admission resources, NetworkPolicies, and standard
  Ingress objects.
- The backend renderer maps validated Terraform outputs into chart values.
  Terraform outputs are derived state, not new operator intent and not a second
  settings model.
- Secret payloads are synchronized out of band into provider secret stores or
  Kubernetes Secrets. Helm values and Helm release history contain only public
  values and secret references.

EKS worker nodes and workload ENIs stay in private subnets with no public IP.
The cluster endpoint is private or explicitly allowlisted and reached by the
existing protected runner/operator path. Do not make the API or nodes public to
avoid solving runner connectivity. Cluster access and `kubeconfig` are
short-lived deploy artifacts, not committed config or bundle metadata.

GitHub Actions OIDC, EKS workload identity, and Cognito/OIDC are three separate
trust boundaries:

- GitHub OIDC authorizes the protected deploy workflow.
- EKS workload identity authorizes an exact namespace/service-account subject
  to call AWS APIs.
- Cognito/OIDC authenticates users to Shifter.

Do not reuse one role or issuer policy for another. Workload roles remain split
by process responsibility and bind exact service-account subjects and audience;
no node-wide application credential, wildcard namespace trust, static AWS key,
or shared cluster-admin application role is acceptable.

The AWS platform renderer must emit the existing canonical runtime bindings,
including explicit `CLOUD_PROVIDER=aws`, Cognito/OIDC configuration, provider
resource identifiers, ECS task-runner placement, and Secrets Manager
references. Deployed startup must never rely on the AWS compatibility defaults
in `entrypoint.sh`, `entrypoint-lib.sh`, or Django settings.

### Kubernetes and edge boundary

Every supported chart render, including AWS, remains subject to ADR-006:

- restricted Pod Security labels on `shifter-platform` and `shifter-jobs`;
- pod seccomp `RuntimeDefault`;
- non-root positive user/group IDs;
- read-only root filesystems, no privilege escalation, and drop `ALL`;
- explicit resource requests/limits and bounded writable volumes;
- default-deny ingress and egress in every Shifter namespace; and
- least-privilege service accounts with API-token automount disabled except
  where a reviewed Kubernetes API client requires it.

For the initial EKS packaging, the GCP provisioner-launcher Deployment,
Job-launch RBAC, provisioner ServiceAccount, and GCPTaskRunner admission policy
are disabled as one capability. Do not render a token-bearing idle launcher or
generalize its GCP-labelled admission contract to AWS without an actual
Kubernetes Job task-runner design and equivalent denial/conformance tests.

Ingress, identity annotations, service annotations, provider API egress, load
balancer source ranges, Kubernetes API CIDRs, and private service CIDRs are
bundle projections. Replace GCP-shaped generic names such as
`gclbSourceRanges` and `googleApiCidrs` with provider-neutral chart concepts;
retain any old names only as documented read-side compatibility aliases with
conflict rejection.

The AWS edge must preserve HTTPS-only public access, a real deployment
hostname, certificate validation, WAF policy, health checks, and private
targets. The GCP overlay continues to preserve managed TLS, Cloud Armor,
BackendConfig, and GCE ingress behavior. An empty source list, disabled policy,
or absent certificate must not silently widen access or select a development
edge.

#### Narrow edge projection contract (#1823)

Issue #1823 is a projection change inside the existing chart boundary, not a
new edge abstraction. The standard `Ingress`, `edge.ingress` values,
`services.*.annotations`, and the existing GCP capability gates are the
incumbent seams. Templates must not branch on `provider.name`, and the change
must not add an `awsAlb` values schema, a second ingress template, or AWS
certificate/WAF Kubernetes objects. ACM and WAF remain Terraform-owned; the AWS
Load Balancer Controller associates them from the rendered Ingress annotations.

`scripts/bootstrap/aws_eks.py:render_aws_values` is the production materializer.
It derives the hostname, ACM ARN, WAF ARN, source CIDRs, and workload-role ARNs
from validated root config and Terraform outputs. The checked-in
`values-aws-dev.yaml` is a non-operational render scaffold with placeholders,
not a competing production configuration. Where the current values surface
carries ACM/WAF identity both as explicit `edge` fields and as controller
annotations, the renderer and contract tests must keep those representations
equal; do not introduce a third copy or let operators author the production
copies independently.

Keep the following layer distinctions explicit:

- `identity.serviceAccountRoleArns` is the IRSA projection consumed by
  `serviceaccounts.yaml`; do not duplicate those role ARNs in the raw
  `serviceAccounts.*.annotations` maps.
- `network.ingressSourceCidrs` governs pod-layer NetworkPolicy and is not, by
  itself, an ALB listener/security-group source restriction. Any ALB
  `inbound-cidrs` projection must come from the same validated Terraform output
  rather than a second allowlist.
- Portal and Guacamole target groups have different canonical health paths and
  success behavior (`/health/` with `200`; `/guacamole/` with `200,302`) in the
  existing AWS ALB modules. Reuse the existing per-Service annotation seam so
  one ingress-wide default does not make either target unhealthy. Preserve the
  established Guacamole stickiness and connection-drain posture rather than
  treating path routing as the whole edge contract.

GCP compatibility is a byte contract, not only a resource-presence assertion.
Use one pinned Helm version, release name, and command shape to freeze the
`values-gcp-dev.yaml` and `values-gcp-prod.yaml` renders before changing shared
templates; both rendered byte streams must remain unchanged. Every checked-in
provider scaffold and generated AWS projection must also pass the chart schema,
Helm lint/template, ADR-006 render guards, kube-linter, and strict kubeconform.

### Image and secret handling

Every deployed image is a fully qualified `repository@sha256:<digest>`.
Backend renderers accept verified digests, not merely non-`latest` tags.
ADR-037 attestation verification for the fixed repository and exact digest
must complete before Helm mutates a release. The chart should represent image
identity without reconstructing a tag-shaped string.

Two current paths are compatibility debt, not precedents for the canonical
flow: `_gcp-dev.yml` still deploys workloads with `kubectl apply -k`, while the
bootstrap Helm renderer accepts a non-`latest` tag. #1324 must converge the
credentialed GCP and AWS paths on Helm plus verified digest identity; Kustomize
may remain validation/parity evidence but not a second workload deploy owner.

Reuse `entrypoint.sh` and `entrypoint-lib.sh` for provider secret hydration.
The chart may carry secret references in ConfigMap-backed runtime env, but
payloads belong in a Kubernetes Secret or AWS Secrets Manager and enter the
process only through the existing hydration boundary.

Do not copy the current workflow pattern that passes secret payloads via
`kubectl create secret --from-literal=...`: those values are visible in process
argv. Secret synchronization must use stdin or a protected temporary file,
avoid shell interpolation, set restrictive permissions, and clean up. Do not
print root config, Helm values, environment maps, Terraform output objects,
Kubernetes Secret manifests, provider responses, or kubeconfig content.

## Canonical Incumbents To Reuse

| Concern | Canonical incumbent | Required reuse |
| --- | --- | --- |
| Root config and sanitized validation | `shifter/installation/schema.py`, `loader.py`, `errors.py` | Parse once; reject duplicate/merge keys, unknown fields, invalid backend/profile/domain, and raw-looking secret material without echoing values. |
| AWS settings and shared policy | `AwsSettings`, `BackendBundle.validate_settings`, `secret_reference_issues`, `installation.range_egress` | Extend the closed AWS intent model only for durable operator intent. Do not copy Terraform inputs or redeclare cross-backend egress. |
| Bundle/publication contract | `installation.contract`, `registry.py`, `publication.py`, `published_contract/` | Reuse existing metadata, output sensitivity, safe argv, compatibility, and drift gates. |
| Generated runtime ownership | `GeneratedOutput`, `ProcessRole`, `installation.runtime_inventory`, `config/_env_manifest.py`, `config/env-manifest.json` | Extend the existing inventory for AWS Helm runtime keys; never add a second AWS env schema or hand-edit the generated manifest. |
| Deploy prerequisite semantics | `scripts/bootstrap/preflight.py`, `bootstrap_core.py`, `docs/dev/deploy-secrets.md` | Add EKS prerequisites to the shared declarative gate and its documentation-parity tests, not workflow-only checks. |
| Deployment execution | `scripts/bootstrap/cli.py`, focused modules behind `scripts/bootstrap/deploy.py`, `terraform_backend.py`, `terraform_deploy.py` | Add an AWS EKS owner behind the existing facade/CLI conventions; workflows invoke it rather than duplicate orchestration. |
| Terraform validation/state | `platform/terraform/validation-inventory.yaml`, `scripts/check_tf_roots`, `scripts/terraform/render_aws_backend_configs.py`, `.tflint.hcl`, `platform/terraform/.checkov.yaml` | Register each new root/toolchain, preserve remote-state encryption/access/locking and saved-plan apply, and run existing policy gates. |
| Helm packaging | `platform/charts/shifter`, `ensure_gcp_control_plane_namespaces`, GCP bootstrap `helm upgrade --install --atomic --wait`, ADR-006 chart render guards | Generalize the namespace/PSS and Helm release mechanics where semantics match while keeping provider authentication/rendering separate; do not create an AWS chart or ad hoc `kubectl apply` path. |
| Kubernetes enforcement | `scripts/adr_guard` Kubernetes checks, `.kube-linter.yaml`, kube-linter, kubeconform, Helm lint, `test_gcp_job_launcher_manifests.py` | Add the AWS values render to every supported-values matrix and preserve GCP admission parity where that capability is enabled. |
| AWS cloud adapters | `shifter_platform.shared.cloud.aws`, `engine/provisioner/cloud/aws`, `engine.ecs`, `config/_cloud.py` | Keep storage, queues, secrets, event bus, database auth, network inventory, and ECS task delivery behind existing protocols/factories. |
| Identity | ADR-009, `config/_oidc_settings.py`, `config/oidc.py`, `management/services.py`, Cognito Terraform | Keep user identity independent of backend and EKS workload identity; preserve issuer/subject, verified-email, MFA, bootstrap, and session gates. |
| Secret hydration | `entrypoint.sh`, `entrypoint-lib.sh`, AWS Secrets Manager/KMS, existing Guacamole secret reference | Pass references through rendered config and fetch payloads at the existing boundary; no values in Helm history, argv, or logs. |
| Errors and observability | `InstallationConfigError`, both existing cloud exception families, `shared.errors`, `shared.api.errors`, `shared.log_sanitize`, provisioner `log_redact`, `config._posture` | Keep config/deploy/runtime errors distinct and public envelopes fixed/sanitized. Reuse structured non-secret posture and existing health/smoke logic. |
| Durable state and delivery | Terraform remote state, Engine resource state, `ProvisionerLaunchIntent`, range outbox/reconciler | Packaging creates no new application persistence selector, event schema, repository, or retry mechanism. |
| Functional readiness | `/health/`, `run_post_deploy_smoke`, existing range smoke domain logic | Use EKS/AWS execution transport around the shared smoke behavior; registry health metadata is not proof of deployment or range conformance. |

## Cross-Cutting Layers The Intended Design Must Pass

1. **Root YAML shape:** `load_root_config` validates `backend: aws`, profile,
   domain, settings, and secret references before any renderer or cloud call.
2. **Bundle/publication shape:** the AWS registry entry validates through the
   same `BackendBundle` and published portable validator as GCP. Contract drift
   or incompatible changes follow the version/migration policy.
3. **Shared preflight:** required tools, AWS account/region, protected deploy
   role, state backend, cluster access path, DNS/certificate, and secret
   references fail before Terraform or Helm mutation.
4. **Workflow trust:** explicit invocation selects a validated config/profile.
   Protected environments, least GitHub permissions, GitHub OIDC trust,
   SHA-pinned actions, concurrency, and no credentialed PR job remain in force.
5. **Terraform/state policy:** the EKS roots are registered and pass
   init-without-backend/validate, TFLint, blocking Checkov, custom IAM/network
   checks, remote-state encryption/access/locking, saved-plan apply, and
   fail-loud postconditions.
6. **Cluster and workload identity:** private placement and cluster access are
   validated; exact service-account trust and least-privilege policies replace
   node credentials or static keys.
7. **Helm value shape:** `values.schema.json` rejects incomplete/incompatible
   edge, identity, image, secret-reference, feature, and CIDR combinations.
   This is deployment-shape validation, not another operator schema.
8. **Kubernetes policy:** every provider render passes Helm lint/template,
   ADR-006 security-context/default-deny checks, kube-linter, strict
   kubeconform, RBAC/admission structural tests, and namespace PSS assertions.
9. **Supply-chain identity:** every image digest is verified and attested before
   `helm upgrade --install --atomic --wait`; no tag or first-deploy bypass.
10. **Runtime env and secret binding:** generated outputs, runtime inventory,
    env manifest, Helm ConfigMap keys, AWS ECS forwarding, and entrypoint
    hydration agree on key ownership and sensitivity. Secret values land only
    in allowed secret destinations.
11. **OS/process exposure:** subprocesses use argv arrays without a shell.
    Credentials, root config bodies, tfvars, secret references/values,
    Terraform output blobs, Helm values, kubeconfigs, and Secret manifests do
    not appear in argv or logs; protected temporary artifacts are bounded and
    cleaned.
12. **Runtime auth and service boundaries:** Cognito/OIDC retains its existing
    verification/authorization path, and AWS service operations continue
    through the existing portal and standalone-provisioner protocol families.
13. **Error envelopes and observability:** installation failures stay
    `InstallationConfigError`; cloud adapters keep their current process-local
    exceptions; operator deploy failures are bounded/sanitized; HTTP,
    WebSocket, health, and event surfaces never expose raw provider,
    Kubernetes, Terraform, config, or secret details.
14. **Persistence and migration safety:** Terraform state and application
    ownership remain explicit. Backend selection is process config, not a DB or
    event field. Existing outbox, launch-intent, retry, and reconciliation
    semantics are unchanged.

## CI, Conformance, And Migration Guardrails

The CI backend matrix validates bundle examples and their owned artifacts; it
does not perform cloud deployment for every matrix row. Its source is the
installation registry plus validated examples, not another hard-coded backend
or profile list. Provider-credentialed deployment remains in explicit,
protected jobs that call the same bootstrap entrypoint as local deployment.
Path filters may optimize work only after backend coverage and explicit target
selection are established.

AWS EKS evidence is layered:

- root config, closed settings, required secrets, bundle metadata, published
  contract, runtime inventory, and example validation;
- Terraform root inventory, formatting, validation, TFLint, Checkov, and custom
  security checks;
- chart schema/lint/render for every supported AWS profile and GCP overlay;
- ADR-006, admission/RBAC, kube-linter, and kubeconform checks on rendered
  output;
- verified digest, atomic Helm rollout, namespace/identity/network/edge
  assertions, and HTTPS health; and
- shared post-deploy smoke plus the applicable ADR-039 AWS range-substrate
  conformance evidence.

Platform packaging conformance and ADR-039 range-substrate conformance are
separate. Passing Helm checks does not prove range lifecycle behavior, and
passing the AWS range adapter suite does not prove EKS security or rollout.

The ECS-to-EKS compatibility document must describe parallel state ownership,
data dependencies, cutover, rollback, and retirement criteria. Do not move,
import, rename, or destroy existing ECS/ASG resources or state as an incidental
effect of creating the EKS bundle.

Running old and new control planes against one database/queue set can create
duplicate schedulers, consumers, outbox drainers, migrations, and range
launches. A migration must designate one active writer/consumer set, drain or
disable the other, serialize database migrations, verify compatibility, cut
traffic deliberately, and retain a bounded rollback path. DNS health alone is
not sufficient cutover evidence.

## Whole-Repository Scope

The following surfaces are in scope for design and validation:

- `shifter/installation/{schema,loader,errors,contract,registry,publication,runtime_inventory,render,doctor,cli}.py`
  and its examples, published contract, docs, and tests;
- `scripts/bootstrap/**`, `scripts/terraform/**`, and
  `docs/dev/deploy-secrets.md`;
- the new AWS EKS Terraform roots/modules plus
  `platform/terraform/validation-inventory.yaml`,
  `.tflint.hcl`, `platform/terraform/.checkov.yaml`, and applicable
  `scripts/check_tf_*` gates;
- `platform/charts/shifter/**`, all supported values files,
  `platform/k8s/gcp/**` while it remains parity/supporting evidence,
  `.kube-linter.yaml`, `.pre-commit-config.yaml`, and Kubernetes/chart tests;
- `.github/workflows/deploy.yml`, reusable provider workflows,
  `.github/workflows/_quality.yml`, workflow semantic tests, `actionlint`, and
  path filters;
- `shifter/shifter_platform/config/**`, `entrypoint.sh`,
  `entrypoint-lib.sh`, portal/worker Docker inputs, cloud factories/adapters,
  `engine/ecs.py`, shared log/error surfaces, and built-image smoke;
- `shifter/engine/provisioner/**` only where AWS task delivery/runtime bindings
  or image provenance are consumed; and
- ADR-006, ADR-007, ADR-009, ADR-011, ADR-035, ADR-037, ADR-039, ADR-044,
  `scripts/adr_guard/**`, and compatibility/operator documentation.

## Gotchas And Anti-Patterns

- Do not create `backend: aws-eks`, an AWS chart fork, a second backend
  registry, or another root/settings/runtime-env schema.
- Do not conflate platform compute (EKS), task transport (ECS or Kubernetes
  Job), range substrate (AWS Terraform), cloud backend (`aws`), identity
  provider (`oidc`), deployment profile, or persisted resource provider.
- Do not render GCP Job-launch RBAC/admission on EKS when AWS still uses the ECS
  task runner. A token-bearing unused launcher is a privilege regression.
- Do not put provider defaults or raw provider objects into neutral
  `values.yaml`; do not make chart templates discover Terraform state or parse
  `shifter.yaml`.
- Do not make arbitrary unvalidated annotation maps the only security contract.
  Backend renderers and chart schema must require the security-relevant
  hostname, TLS, WAF/edge, identity, and CIDR combinations.
- Do not put secret payloads, kubeconfig, Terraform plans/outputs, tfvars, or
  complete generated Helm values in bundle metadata, Helm history, ConfigMaps,
  argv, artifacts, comments, logs, health responses, events, or public errors.
- Do not pass secret values with `kubectl --from-literal`; do not use
  shell-composed Helm/Terraform commands or echo generated config.
- Do not deploy tags or reconstruct `repository:tag` after digest verification.
- Do not use public nodes, public pod IPs, broad cluster endpoint access,
  node-instance credentials, wildcard workload-identity trust, cluster-admin
  application roles, or one workload role shared by all services.
- Do not use branch/ref names, Terraform directories, Helm values filenames, or
  compatibility aliases to select deployment behavior.
- Do not turn `ValidationCheck` or doctor into a deployment engine. Do not
  duplicate bootstrap semantics in workflow YAML.
- Do not let the EKS root share or take ownership of legacy ECS state. Do not
  run both control planes as active consumers during migration.
- Do not weaken GCP controls while neutralizing the chart. GCP managed TLS,
  Cloud Armor, Workload Identity, private control-plane access, and admission
  parity remain required.
- Do not claim EKS, Helm, or bundle maturity from metadata alone. Retain
  rendered, policy, rollout, health, smoke, and real-provider evidence.

## Non-Goals And Implementation Boundaries

- No implementation, cloud mutation, deployment, or migration is part of this
  preflight.
- No replacement of the AWS range-substrate adapter, ECS provisioner task
  transport, or ADR-039 lifecycle contract.
- No redesign of Cognito/OIDC, account binding, MFA, session policy, bootstrap
  authorization, or user-facing auth APIs.
- No redesign of Django cloud protocols, standalone-provisioner protocols,
  persistence models, repositories, public DTOs, events, outbox/reconciliation,
  task idempotency, or exception/logging frameworks.
- No in-place Terraform state move, ECS/ASG resource rename/removal, database
  migration strategy change, or compatibility alias cleanup without its own
  reviewed migration.
- No Azure/local backend, generic multi-cluster orchestrator, GitOps controller,
  service mesh, external-secret operator, autoscaling redesign, or chart
  marketplace publication.
- No claim that Kubernetes packages live-fire ranges. EKS is the Shifter
  management plane; ADR-030 and ADR-039 continue to govern range containment.
