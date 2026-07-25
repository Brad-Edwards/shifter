# Terraform Module Decomposition Preflight (#688)

Status: pre-implementation guidance

Date: 2026-07-25

Tracking issue: <https://github.com/Brad-Edwards/shifter/issues/688>

This is a requirement-free architecture preflight. The GitHub issue is the
shipping contract. This note fixes the boundaries for decomposing the current
Terraform estate; it does not implement the refactor or prescribe a commit
sequence.

## Scope Boundary And Current Estate

The migrated issue's line counts no longer describe the whole current tree.
The GCP `platform-core/main.tf` facade is now 326 lines, the former summit NGFW
roots are absent, and the committed 932-line victim CIDR blobs have already
been replaced by the validated `shifter.yaml` range-egress contract and
gitignored generated bridge files.

The current tracked Terraform files above 500 lines, excluding `.terraform`
artifacts, are:

| File | Current LOC | Architectural owner |
| --- | ---: | --- |
| `platform/terraform/global/iam/github-oidc.tf` | 1510 | GitHub OIDC trust, deploy identities, permission boundary, and category policies |
| `platform/terraform/modules/engine-provisioner/iam.tf` | 1238 | Engine execution/task identities and provisioner privileges |
| `platform/terraform/environments/{dev,proof}/portal/main.tf` | 1195 each | AWS portal root composition |
| `platform/terraform/environments/prod/portal/main.tf` | 1161 | AWS portal root composition |
| `platform/terraform/modules/portal/ec2/main.tf` | 983 | Portal host identity, network, launch, and scaling |
| `platform/terraform/environments/{dev,proof}/portal/variables.tf` | 907 each | Public root input contracts |
| `platform/terraform/environments/prod/portal/variables.tf` | 818 | Public root input contract |
| `platform/terraform/modules/range/vpc/firewall.tf` | 576 | Range Network Firewall rules, policy, logging, and routing |

`platform/terraform/gcp/modules/platform-core/variables.tf` is a borderline
496 lines. It is the cohesive public input contract for an already decomposed
facade, so it may remain intact for this issue if its ownership rationale is
recorded in the implementation PR; it must not absorb resources or provider
logic. `platform/terraform/modules/portal/ec2/user_data.sh` is 557 lines but is
not Terraform and is already separated from the HCL resource graph. Shell
runtime decomposition is not part of this issue.

All current hotspots are in scope even when they were not in the migrated
issue's evidence table. Proof must follow the same portal composition boundary
as dev and prod; leaving it as a third copy would recreate the accepted
duplication under another environment name.

## Architecture Decisions And Guardrails

### Preserve module identity for concern-only splits

The oversized engine-provisioner IAM, portal EC2, range firewall, and global
OIDC IAM files should be partitioned into sibling `*.tf` files inside their
existing Terraform module or root. Terraform loads all sibling files as one
module, so resource and data labels, module labels, provider bindings,
`count`/`for_each` keys, dependencies, lifecycle rules, outputs, and state
addresses remain unchanged.

Partition by the boundary a reviewer is authorizing:

| Surface | Reviewable boundaries |
| --- | --- |
| Global GitHub OIDC IAM | OIDC provider/trust and roles; image-pipeline identity; CI permissions boundary; compute; networking; data; security; management; attachments/outputs; historical moves |
| Engine-provisioner IAM | execution role; task role state/data/secrets; EC2 describe/mutation; `RunInstances`; GWLB/ELBv2; endpoints/bootstrap; SSM Run Command; KMS; dynamic Polaris/VPN role management |
| Portal EC2 | log group/alarms/metric filters; instance role and privilege groups; security group; launch template and single-instance path; ASG/scaling/lifecycle |
| Range firewall | firewall subnets/routing; domain and fixed service rule groups; CIDR allowlist rule groups; policy/firewall; logging; private default routes |

Do not create child modules merely to shorten these files. An IAM policy file,
firewall rule file, or launch-template file is an ownership view inside one
existing lifecycle boundary, not a separately reusable service. Child modules
are warranted only where composition is reused as a real contract.

### Make AWS portal composition canonical once

The dev and proof portal `main.tf` files differ by only two lines, and prod is
the same composition with the optional CTFd/warm-pool surface removed. The
canonical reusable unit is one AWS portal composition module under
`platform/terraform/modules/portal/`, organized by the same existing resource
boundaries. It is an AWS composition facade over the existing `portal/*`,
`engine-provisioner`, `guacamole`, and `log-aggregation` modules; it is not a
generic cross-cloud "platform" abstraction.

The environment roots remain the owners of:

- Terraform/provider declarations and committed lockfiles;
- backend configuration and state keys;
- `terraform_remote_state` reads from the foundation and range roots;
- deployment-secret rendering into root `local.auto.tfvars`;
- the existing public variable names, types, defaults, and validation;
- explicit mapping of root inputs and remote-state outputs into the
  composition module; and
- the existing root output names and sensitive flags, proxied from the
  composition module.

The composition module owns the repeated resource graph and its internal
wiring. It receives explicit values; it must not receive an opaque
`terraform_remote_state` object, backend credentials, a raw tfvars payload, or
an untyped `map(any)` escape hatch. Optional CTFd and other environment
variations stay on their existing typed variables and conditional resource
keys rather than being inferred from `environment == "prod"`.

Root variable declarations may be split into concern-named sibling files
without renaming variables. They are deployment env-binding contracts, not a
second application DTO. Keep defaults and operator-facing validation
authoritative at the roots. The child module should use exact Terraform types
and validate only invariants required for safe direct reuse; do not copy
defaults or maintain two divergent policy validators.

### Treat state and outputs as compatibility contracts

Moving a block between sibling files is state-neutral. Moving any root resource
or module call beneath the shared portal composition module changes its address
and therefore requires declarative `moved` blocks in each dev, proof, and prod
root. Use the GCP decomposition precedent at
`platform/terraform/gcp/modules/platform-core/moved.tf`: enumerate every
relocated resource/module address, preserve `count`/`for_each` identity, and
keep the move declarations in source control.

Do not use ad hoc `terraform state mv`, import, delete/recreate, `-target`, or
manual console repair as the normal migration. Preserve and extend historical
move chains, including `module.pulumi_provisioner` to
`module.engine_provisioner`; do not delete old `moved` blocks merely because
the current live states are believed to have crossed them.

The portal root outputs are operational APIs consumed by deploy verification,
inspection assertion, RDS pending-modification checks, bootstrap walkthroughs,
and portal deployment tooling. Preserve every current output name, value
shape, sensitive flag, and environment-specific optional-output behavior.
Similarly, preserve remote-state input keys, backend keys, provider
constraints, and lockfiles unless a separate migration explicitly changes
them.

Every credentialed environment plan must demonstrate an address-only migration:
no destroy/create, replacement, policy broadening, route churn, secret-version
replacement, KMS-key replacement, or output removal caused by decomposition.

### Keep the existing CIDR contract; do not invent another data source

The large committed allowlist problem is already solved by the canonical
provider-neutral `shifter.yaml.settings.range_egress` schema,
`shifter/installation/range_egress.py`, and `shifter-config render`.
Deployment-specific values remain uncommitted and render to
`victim_allowed_cidrs.auto.tfvars` for AWS or
`range_egress.auto.tfvars` for GCP. Terraform variable validation rechecks the
direct-apply boundary.

The firewall split may relocate the existing HCL rule blocks, but it must not
introduce a CSV/JSON/YAML allowlist, a second generator, checked-in live CIDRs,
or a provider-specific public configuration schema. The ownership/update
process remains `docs/architecture/range-egress-ip-allowlist.md` and
`docs/dev/deploy-secrets.md`.

## Canonical Incumbents To Reuse

| Concern | Canonical incumbent | Boundary to preserve |
| --- | --- | --- |
| GCP decomposition and state migration | `platform/terraform/gcp/modules/platform-core/main.tf`, `moved.tf`, and provider-native child modules | Reuse facade plus declarative-move semantics. Do not copy GCP resource concepts into the AWS composition facade. |
| Terraform estate ownership | `platform/terraform/validation-inventory.yaml`, `scripts/check_tf_roots/` | Existing roots remain roots with their lockfiles/toolchain profile. Register a new reusable portal composition module; do not create a lockfile for it. |
| Root validation | `.github/workflows/_quality.yml` backendless locked init/validate matrix | Validate every registered root after module-source changes. This is distinct from plan and security scanning. |
| Lint and static security | `.tflint.hcl`, `platform/terraform/.checkov.yaml`, Checkov, `terraform fmt`, `terraform validate` | Keep all layers blocking. A file move does not authorize a waiver or narrower scan. |
| Repo-specific IAM/network policy | `scripts/check_tf_*/`, `scripts/check_portal_target_sg_sources/`, and their unit tests | Make checks follow the owning module or aggregate sibling files; never let an exact old filename become an accidental bypass. |
| Architecture policy and waivers | ADR-004 in `docs/adr/index.yaml`, `docs/adr/exceptions.yaml`, `scripts/adr_guard/adr_guard.py` | Preserve Checkov waiver semantics, expiry, secret/tfvars rules, generated-artifact rules, and live-identifier hygiene. Update path evidence when files move. |
| AWS deploy routing | `.github/workflows/deploy.yml`, `_shifter-platform.yml`, `_range.yml`, `_core.yml` | Existing directory globs, environment selection, state locking, saved-plan apply, and deployment-secret rendering remain authoritative. |
| Portal operations | `scripts/portal_deploy/`, `scripts/assert_portal_inspection/`, `scripts/check_rds_pending_modifications/` | Root output envelopes and Terraform working directories remain stable. |
| Range egress policy | `shifter/installation/range_egress.py`, `shifter-config render`, `docs/architecture/range-egress-ip-allowlist.md` | One provider-neutral schema and generated provider bridges; no committed deployment allowlists. |
| Review ownership | `.github/CODEOWNERS`, `platform/terraform/validation-inventory.yaml` owners | File boundaries improve review, but do not invent a parallel ownership registry. |

There is no application controller, DTO, service, repository, persistence
model, application exception hierarchy, or logger to reuse for this refactor.
Terraform module inputs/outputs, remote state, and Terraform state addresses
are the relevant contracts. Adding application-layer abstractions would cross
the wrong boundary.

## Filename-Coupled Consumers That Must Move Atomically

The current enforcement estate contains exact-path assumptions. A split is not
complete while any of these still scans only the former monolith:

| Owning surface | Coupled consumers |
| --- | --- |
| Engine-provisioner IAM | `check_tf_iam_ec2_scope`, `check_tf_iam_elb_scope`, `check_tf_iam_ssm_scope`, `check_tf_kms_secrets_grant`, `check_tf_iam_role_naming`, their live-file tests, `.pre-commit-config.yaml`, `.github/workflows/_quality.yml`, ADR evidence/exceptions |
| Portal EC2 | `check_tf_kms_secrets_grant`, `check_tf_iam_role_naming`, portal deploy path-routing tests, Checkov exceptions, architecture evidence |
| Portal root composition | `check_portal_target_sg_sources`, pre-commit/CI invocations, deploy routing tests, inspection/RDS/portal output consumers |
| Range firewall | `scripts/terraform/tests/test_range_firewall_dns.py`, ADR-017/range-isolation evidence, Checkov exceptions |
| Global OIDC IAM | `check_tf_iam_role_naming` canonical-path checks, IAM deploy/bootstrap tools, ADR-004-R22/R25 evidence and Checkov exceptions |

Prefer module-directory inputs with deterministic sorted sibling-file
aggregation when a policy spans files. Update pre-commit trigger globs and CI
explicit inputs together. `check_tf_kms_secrets_grant` is currently
intentionally per-file and omits cross-file aggregation; either keep each role
and all of its attached policy blocks in one review file or extend the checker
and tests to reason across the module before separating them. Passing because
the role and its required KMS grant landed in different files is a security
regression, not successful decomposition.

Path changes in `docs/adr/exceptions.yaml` must preserve the exact policy,
owner, reason, and expiry. Do not broaden an exception from one resource file
to an entire Terraform estate merely to avoid maintaining path evidence.

## Cross-Cutting Layers The Design Must Pass

| Layer | Required behavior |
| --- | --- |
| Cloud authorization | No new principal or trust path. AWS applies continue through the existing GitHub OIDC roles and permission boundaries. OIDC subjects, `iam:PassRole`, permissions-boundary tamper denial, service conditions, ownership tags, and policy attachment caps remain byte-semantically equivalent unless a separate security change says otherwise. Pull-request validation remains credential-free on GitHub-hosted runners. |
| Secret handling | Secret values stay in GitHub secrets, Secrets Manager, transient gitignored `local.auto.tfvars`, or Terraform sensitive state. Preserve Secrets Manager CMKs, KMS aliases/grants, `kms:ViaService`, and sensitive outputs. Do not print or commit tfvars, state, plans, secret versions, policy payloads containing values, or rendered environment files. |
| Env-binding shape | Preserve root variable names/types/defaults/validation and the `TF_VARS_<ENV>_PORTAL` rendering contract. Remote-state values are mapped explicitly into typed module inputs. Range CIDRs continue through `shifter.yaml` plus `shifter-config render` and are revalidated by Terraform. |
| Terraform parser/state gate | Run format, TFLint, backendless locked init/validate for all roots, and module contract tests where registered. Credentialed plans must verify declarative moves and no behavioral infrastructure change. Lockfiles and backend keys remain root-owned. |
| Static policy gates | Checkov remains blocking through the single `.checkov.yaml`; repo-specific IAM, KMS, SG, CIDR, RDS, OIDC, and ADR checks retain or broaden their file coverage to the new sibling files. No soft-fail or new skip is justified by decomposition. |
| Generated-artifact and identifier gates | ADR-004-R7/R8/R14, gitleaks, `.gitignore`, and ADR guard continue to reject plaintext tfvars secrets, plans/state, generated allowlist bridges, live account/network identifiers, and public operator CIDRs. A move must not expose content previously kept out of source. |
| OS/process exposure | The refactor adds no runtime process. Existing workflows keep tfvars in workspace files rather than command arguments, keep `TF_LOG` off, use saved plans under existing protections, and never dump environment/state for diagnostics. No shell `eval` or HCL code generation is introduced. |
| Error envelope | Failures remain Terraform/Checkov/TFLint/repo-checker diagnostics in CI. They may name a module, resource address, file, line, check, and environment; they must not echo secret inputs, state values, rendered tfvars, full sensitive plans, or provider debug payloads. No application exception hierarchy is introduced. |
| Logging and observability | Existing CloudWatch log groups, metric filters, alarms, dashboards, retention, KMS bindings, SNS actions, and Bedrock logging resources retain addresses and settings while moving to concern files. GitHub job status and bounded Terraform diagnostics remain the refactor evidence surface; no second logging pipeline is added. |
| Persistence | Remote Terraform backends and state are authoritative. Sibling-file moves are address-neutral; composition-module moves use checked-in `moved` blocks. Remote-state outputs and portal root outputs remain stable. No database or application persistence change is involved. |
| Exceptions | Terraform/provider failures propagate nonzero. Existing Checkov exceptions remain expiring ADR records with updated narrow paths. Do not add catch-all ignores, `continue-on-error`, targeted applies, or an application-style exception layer. |

## Extensibility Seam

The seam is the AWS portal composition module's explicit typed inputs and
outputs, with environment roots retaining backend, remote-state, and deployment
binding. A future AWS staging environment should be another thin root that
selects backend/tfvars and calls the same composition contract, not a copied
`main.tf`. A future optional portal capability should be an explicit typed
input and stable output on that module, not an environment-name conditional or
an opaque settings map.

For IAM and firewall growth, the seam is the existing principal, privilege,
traffic-path, or policy-lane boundary expressed as sibling files inside the
same module. The next policy statement should extend its owning boundary
without creating another role, policy generator, firewall abstraction, or
stateful child module.

Security checker inputs must follow module ownership rather than a fixed list
of historical filenames. That lets another sibling policy file be covered
without editing several unrelated CI command lines, while deterministic
aggregation and focused checker tests keep the scope reviewable.

## Whole-Repo Surfaces In Scope

The implementation may need to update the relevant subset of:

- `platform/terraform/environments/{dev,proof,prod}/portal/**` for thin roots,
  split public variable contracts, output proxies, and state moves;
- `platform/terraform/modules/portal/**`,
  `platform/terraform/modules/engine-provisioner/**`,
  `platform/terraform/modules/range/vpc/**`, and
  `platform/terraform/global/iam/**` for concern ownership;
- `platform/terraform/validation-inventory.yaml` for the new reusable module;
- `.pre-commit-config.yaml`, `.github/workflows/_quality.yml`, and the named
  `scripts/check_tf_*`, portal-SG, firewall-DNS, and routing tests for complete
  policy coverage;
- `platform/terraform/.checkov.yaml`, `docs/adr/index.yaml`,
  `docs/adr/exceptions.yaml`, and ADR enforcement docs only where path evidence
  or enforcement changes;
- `.github/workflows/deploy.yml` tests and quality ownership only if a new path
  is not already covered by the existing `portal/**`, `engine-provisioner/**`,
  `range/**`, or `global/iam/**` globs; and
- operator/architecture docs that name a moved ownership file.

Host/runtime layers that see the result are local Terraform/pre-commit,
GitHub-hosted quality runners, self-hosted credentialed deploy runners,
Terraform CLI/providers/backends, AWS IAM/KMS/EC2/ASG/Network Firewall and
portal service APIs, CloudWatch/SNS, and the scripts consuming portal outputs.
Application HTTP handlers, Django models, Kubernetes, GCP runtime workloads,
and databases do not receive a new contract.

## Gotchas And Anti-Patterns

- Do not equate a smaller file with a smaller lifecycle boundary. File splits
  should improve review without multiplying modules, roles, providers, state,
  or outputs.
- Do not rename resources, modules, `for_each` keys, `count` conditions,
  KMS aliases, IAM policy names, log groups, or outputs while moving blocks.
- Do not extract the portal composition without complete per-root `moved`
  declarations and credentialed no-replacement plans for dev, proof, and prod.
- Do not pass whole remote-state output objects, raw tfvars, `map(any)`, or
  provider credentials into the shared composition module.
- Do not infer feature posture from `environment` strings. Preserve explicit
  variables such as `enable_ctfd`, autoscaling, Redis, inspection, and logging
  controls.
- Do not create a generic cross-cloud portal module. GCP already has its own
  provider-native facade and child-module graph.
- Do not copy root defaults and validation into a second drifting schema, and
  do not weaken child inputs to `any` to avoid writing a real contract.
- Do not leave proof on a copied composition path while deduplicating only dev
  and prod.
- Do not let exact-path pre-commit hooks, CI commands, unit fixtures, Checkov
  exception paths, or ADR evidence silently stop covering moved resources.
- Do not separate an IAM role from policies when a checker still aggregates
  only within one file.
- Do not replace `jsonencode` IAM documents with hand-built JSON templates or
  introduce an IAM/firewall code generator. Existing HCL and policy checks are
  the canonical representation.
- Do not reintroduce committed CIDR data, a second allowlist schema, or
  hand-maintained provider bridge values.
- Do not use state surgery, imports, `-target`, taint, resource recreation, or
  deleted move history to force a clean plan.
- Do not broaden Checkov skips, disable TFLint rules, soften CI, or add
  `continue-on-error` to make a structural refactor pass.
- Do not mix security hardening, provider upgrades, resource behavior changes,
  naming cleanup, or output redesign into the decomposition. Those obscure
  whether the state-preserving refactor is behaviorally neutral.

## Non-Goals And Implementation Boundaries

- No Terraform implementation, state operation, workflow change, policy
  change, or cloud mutation in this preflight.
- No redesign of AWS authentication, IAM privileges, KMS ownership, network
  policy, portal topology, observability posture, or deploy sequencing.
- No provider/Terraform upgrade, lockfile refresh, backend migration, workspace
  adoption, or environment-directory consolidation.
- No application controller/DTO/service/repository/schema/exception/logging
  abstraction; none belongs to this concern.
- No new CIDR source, parser, generator, UI, database, or provider-specific
  public egress schema.
- No decomposition of non-Terraform shell payloads such as `user_data.sh`.
- No requirement to split cohesive files below the approximate threshold
  solely to reach an arbitrary line count; any retained near-threshold file
  needs an explicit ownership rationale and must remain within one concern.
- No security-policy behavior change hidden inside file movement. Independent
  findings remain independent issues and must preserve their own migration and
  review evidence.

## Required Validation Evidence

The implementation is not complete without:

- `terraform fmt -check -recursive`;
- `TFLINT_CONFIG="$(pwd)/.tflint.hcl"; cd platform/terraform && tflint --recursive --config "$TFLINT_CONFIG"`;
- `python3 scripts/check_tf_roots/check_tf_roots.py --check` and the registered
  root init/validate matrix;
- blocking Checkov with `platform/terraform/.checkov.yaml`;
- every affected repo-native IAM/KMS/SG/CIDR/firewall checker and its unit
  tests against the new file layout;
- `actionlint` when workflow YAML changes;
- `python3 scripts/adr_guard/adr_guard.py --all --level ci`; and
- credentialed dev, proof, and prod plans showing only declared address moves
  and no infrastructure replacement or behavioral change.
