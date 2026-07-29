# Guardrail Automation Decomposition Preflight (#689)

Issue #689 is a requirement-free maintainability refactor. The GitHub issue is
the shipping contract. The decomposition must preserve the effective workflow
graph, security boundaries, failure semantics, and local CLI contract; reducing
line counts is not sufficient if enforcement becomes harder to prove.

## Architecture Decision

Keep these stable composition roots:

- `.github/workflows/_quality.yml` remains the reusable Quality entrypoint
  called by `deploy.yml`.
- `.github/workflows/_shifter-platform.yml` remains the reusable AWS platform
  entrypoint called by `deploy.yml`.
- `.claude/scripts/validate-trace.py` remains the command-line compatibility
  entrypoint for `extract`, `validate`, and `batch`.

The two workflows have different safe decomposition boundaries:

- Quality is a fan-out/fan-in validation graph. Its only path-classification
  owner remains the `paths` job backed by
  `.github/quality-path-filters.yaml` and
  `scripts/quality_ownership/{contract,classify_paths}.py`. Reusable child
  workflows may own cohesive dependency clusters, but they receive a
  non-secret serialization of the canonical classifier outputs; they do not
  parse changed files or maintain a second route schema. Coverage-producing
  jobs and SonarCloud stay in one dependency cluster so Sonar can still inspect
  their actual results and consume their artifacts. Terraform matrix generation
  and its consumers likewise stay together.
- Platform is an ordered deployment DAG. Split only at existing job/operation
  boundaries while the coordinator retains the current job ids, `needs` graph,
  `if` gates, outputs, and blocking/advisory results. Each called operation owns
  its runner, timeout, least-privilege permissions, GitHub Environment binding,
  explicit secret inputs, and all temporary state needed by that operation.
  Do not create a preparatory workflow whose `GITHUB_ENV`, workspace files, or
  secret-derived outputs are expected to cross a reusable-workflow boundary.

`validate-trace.py` should become a thin CLI over focused parser/extractor,
validation-policy, report/serialization, and CLI modules. Its existing
`FunctionInfo`, `ValidationResult`, and `TraceValidationReport` dataclasses are
the contract types; do not reproduce them as parallel dictionaries, DTOs, or
exceptions in each module. AST extraction owns facts, validation policy owns
claim comparisons, reporting owns aggregation/serialization, and the CLI alone
owns argv, file access, stdout/stderr, and process exit status.

No new ADR is required. ADR-003, ADR-004, ADR-011, and ADR-037 already govern
the affected behavior. Their descriptions, evidence paths, enforcement guide,
and CI/CD documentation must be updated in the implementation when jobs or
steps move, even if behavior is unchanged; otherwise the machine-readable
registry would point at stale evidence.

## Canonical Incumbents To Reuse

| Concern | Canonical owner and invariant |
| --- | --- |
| Top-level routing and branch protection | `.github/workflows/deploy.yml` owns change detection, the reusable `Quality` call, and the always-present `PR Gate`. Keep ordinary docs-only skipping and the aggregate result contract there; do not add another PR gate. |
| Quality path schema | `.github/quality-path-filters.yaml` plus `scripts/quality_ownership/contract.py` are the only ownership/route schema and parser. `classify_paths.py` is the only changed-path classifier. Workflow fragments consume its output; they do not run `git diff`, `paths-filter`, or a second YAML/JSON config. |
| Workflow semantic validation | `scripts/adr_guard/adr_guard.py`'s `_dw_*` workflow-as-data model and `scripts/adr_guard/tests/test_deploy_workflow.py` already parse and evaluate job gates, runner exposure, permissions, environments, and routing. Extend that model to follow local reusable-workflow calls; do not add substring-based workflow tests or another YAML model. |
| Quality ownership reconciliation | The `quality-path-ownership` check and `scripts/adr_guard/tests/test_quality_path_ownership.py` own blocking lint/security/test responsibility, matrix selection, output wiring, and reachability. A moved job remains visible to this same checker. |
| Local/CI command policy | Package `pyproject.toml` / `package.json` files own lint, test, warning, and coverage policy. The root `Makefile` and `docs/dev/testing.md` own clean-checkout test commands. Workflow fragments must not restate coverage floors, warning policy, or dependency versions in a new config. |
| ADR and local guardrails | `.pre-commit-config.yaml`, `scripts/adr_guard/adr_guard.py`, `docs/adr/{index,exceptions}.yaml`, and `docs/technical/dev/adr-enforcement.md` remain the enforcement surfaces. Guardrail changes still require documented evidence, owners, and expiring exceptions where applicable. |
| Terraform policy | `.tflint.hcl`, `platform/terraform/validation-inventory.yaml`, `scripts/check_tf_roots/`, and `platform/terraform/.checkov.yaml` remain authoritative. Terraform Checkov is blocking; Kubernetes Checkov remains separately advisory. |
| Kubernetes policy | `.kube-linter.yaml`, kustomize rendering, and kubeconform remain the manifest validation layers. Do not replace rendered-manifest validation with source-file-only checks during the split. |
| Secret scanning | `.gitleaks.toml`, the always-present `Pre-commit` job, and Quality's `secrets-gitleaks` job remain distinct local/full-history gates. |
| SonarCloud | `sonar-project.properties` owns shared analysis settings; repository variables own project identity; `SONAR_TOKEN` remains the only secret and stays in the scan step environment. Coverage artifact names and paths remain the producer/consumer contract. |
| AWS prerequisite/config validation | `scripts/bootstrap/preflight.py` owns fail-loud prerequisite checks and environment-to-logical-secret-name mappings. `shifter/installation`'s `shifter-config` loader/validators/renderers own `shifter.yaml` shape and backend-derived runtime tfvars. |
| Terraform backend files | `scripts/terraform/render_aws_backend_configs.py` is the workflow-facing renderer. Do not duplicate backend path/key rendering in child workflows. |
| Portal deployment | `scripts/portal_deploy/portal_deploy.py` owns topology resolution, ASG image/worker verification, and post-deploy verification. `scripts/portal-deploy/deploy_portal.sh` remains the single-instance runtime script. |
| Apply-time operational checks | `scripts/handle_sd_replacement`, `scripts/assert_portal_inspection`, and `scripts/check_rds_pending_modifications` remain the focused policy/operation owners around the same saved Terraform plan and apply. |
| Post-deploy smoke | `scripts/smoke-test.sh` and `portal_deploy.py` own AWS transport and smoke semantics. The workflow owns only gating, credentials, issue creation, and advisory result handling. |
| Trace schemas and errors | The three existing trace dataclasses and JSON serialization via `dataclasses.asdict` are the incumbent result envelope. Use ordinary input/parse errors at the CLI boundary; do not create a repository-wide exception hierarchy or logging framework for this local tool. |

## Cross-Cutting Layers

| Layer | Required invariant |
| --- | --- |
| GitHub event/auth surface | Pull requests run Quality on GitHub-hosted runners and never reach a self-hosted/cloud-credentialed platform job. Manual dispatch remains the only deploy event. Nested platform operations repeat a fail-closed PR denial at the job that selects the runner or assumes a role, rather than relying only on an ancestor call condition. |
| `GITHUB_TOKEN` permissions | Quality jobs keep `contents: read` except where an existing capability requires more. Platform child jobs retain the exact least-privilege scopes for OIDC, PR comments, attestations, or failure issues. Nested workflow permissions can only stay equal or become more restrictive, so the intermediate coordinator must not accidentally remove a scope a child requires or replace per-job scopes with `write-all`. |
| Reusable-workflow secret boundary | Pass named secrets explicitly at every nesting level; never use `secrets: inherit`. A child declares only the secrets its operation consumes. Environment-bound jobs keep `${{ inputs.github_environment }}`; remember that a same-named GitHub Environment secret shadows a caller-passed secret. |
| Sonar secret and fork handling | `SONAR_TOKEN` is passed only to the cluster that owns Sonar and reaches the scanner through `env`, never `with.args`, argv, outputs, artifacts, or summaries. The scan remains gated on `github.repository == 'Brad-Edwards/shifter'`, not variable presence, and fork-origin PRs still skip because GitHub withholds the token. |
| Deployment secret selection | Select `dev` / `proof` / `prod` with a closed `case` and fail on an unknown environment or empty active secret. Never use the falsy `A && DEV_SECRET || PROD_SECRET` expression, which can fall through to the wrong environment. Reuse bootstrap's logical-name mapping instead of creating per-workflow environment enums. |
| Config shape | `workflow_call` keeps boolean inputs boolean and strings string. Quality routing is the canonical classifier output map serialized once; it contains no secret material and is reconciled by ADR guard. `shifter.yaml` always passes through `shifter-config`; EKS JSON remains `jq -e 'type == "object"'` validated; Terraform roots remain inventory-validated. |
| Local OS/process exposure | Raw tfvars, root config, and other protected payloads are passed in step environment variables and written to runner-temporary or gitignored files with owner-only mode where supported. Never place their values in process argv, `GITHUB_OUTPUT`, command annotations, artifacts, cache keys, or job summaries. A state bucket name or file path is configuration, not an authentication credential, but must still not be logged gratuitously. |
| Reusable-workflow data lifetime | `GITHUB_ENV`, step outputs, and filesystem state are job/workflow-local. Only validated non-secret values such as an image digest/tag or canonical routing map cross a called-workflow boundary through declared outputs. Do not attempt to export a secret-derived role ARN or protected payload as a reusable-workflow output; GitHub may suppress secret-looking outputs and the boundary would broaden exposure. |
| Terraform plan/apply | Portal apply creates a local `tfplan`, drains Service Discovery against that exact file, applies that same file with the lock timeout, and restores desired counts. Never upload a binary plan, rebuild a plan in another workflow, or replace it with `apply -auto-approve`. |
| Image identity/provenance | Build outputs remain immutable `sha256` digests. Guacamole, portal, and EKS deployment keep SBOM/provenance generation and verify the exact `Brad-Edwards/shifter` image digest before mutation. `GH_TOKEN` stays in the child environment and never argv. |
| Action supply chain | Every non-local action in a cloud-credentialed or self-hosted child workflow remains pinned to a full commit SHA; downloaded executables remain version-pinned and checksum-verified. Local workflow calls resolve from the same commit as the caller. |
| Error envelope and fail behavior | GitHub job conclusions, `::error::` annotations, exit codes, job names, and bounded job summaries are the observability surface. Blocking jobs remain blocking. Only the already-advisory Kubernetes Checkov, Trivy/OSV signal, and post-deploy smoke retain advisory/continue-on-error behavior. |
| Persistence | This change introduces no database, cache, queue, audit store, or workflow-state repository. Workflow artifacts remain limited to the existing coverage, JUnit, and advisory SARIF contracts; protected config and Terraform plans are never persisted as artifacts. |
| Trace file/source boundary | The trace CLI reads text and Python source only at the CLI boundary. Parser/policy modules accept already-provided content or AST facts and perform no filesystem, process, network, or logging work. Batch source paths must be resolved against an explicit repository root before this tool is ever wired to untrusted automation; reject traversal/symlink escape and bound input size rather than reading arbitrary host files. |
| Trace output leakage | Successful JSON shapes remain deterministic. Diagnostics identify the path/field and error class without printing source text or protected values. `extract` intentionally emits code metadata and docstrings, so it must remain an explicit operator action rather than a new automatic log dump. |

## Workflow Compatibility Contracts

The following are behavior, not incidental YAML:

- `deploy.yml` continues to call the two stable composition roots and `PR Gate`
  continues to accept Quality only when it succeeds or is legitimately skipped
  for an ordinary docs-only PR.
- Existing Quality job ids/names, route predicates, matrix members, artifact
  names, blocking/advisory status, service containers, timeouts, and Sonar
  fan-in remain stable unless a separately documented policy change is intended.
- `skip_tests: false` remains literal at the protected-branch caller.
  `inputs.skip_tests` may gate test/smoke jobs only; lint, typecheck,
  architecture, SAST, secret, and infrastructure security gates remain immune.
- Workflow-file or ownership-contract changes still force the full matrix, and
  classifier/guard changes still trigger the independent self-check. Any new
  Quality child containing stack-smoke logic must also be covered by
  `deploy.yml`'s `stack_smoke` change filter; otherwise changing the smoke gate
  could set `run_all` but leave `run_stack_smoke` false.
- Checkov cache keys must hash the fragment that owns the Checkov invocation (or
  the complete Quality workflow family), not only the old root filename.
- The Platform coordinator preserves the current `plan`,
  `push-guacamole-images`, `apply`, `build`, `eks-deploy`, `deploy`, `verify`,
  and `post-deploy-smoke` result/output dependencies. The legacy-disabled gates,
  EKS opt-in, image digest output, and advisory dev smoke remain distinguishable.
- Mutating platform operations keep their GitHub Environment binding, runner
  class, timeout, OIDC/attestation permissions, and explicit named secrets in
  the child that actually executes them.
- The semantic workflow model must recursively discover local `jobs.*.uses`
  calls from the stable roots, detect cycles/path escape, and evaluate the
  effective caller/callee graph. Merely appending new filenames to multiple
  static lists creates the next god-file problem and leaves future children
  unguarded.

## Trace Validator Compatibility Contracts

- The public commands and successful JSON field names remain `extract`,
  `validate`, and `batch`, with `FunctionInfo`, `ValidationResult`, and
  `TraceValidationReport` serialized once.
- Parser/extractor behavior must be characterized before it moves, including
  async functions, methods selected with `class`, decorators, annotations,
  defaults, calls, raises, missing files, syntax errors, and duplicate/nested
  function names. Do not accidentally change AST-walk scope while moving code.
- Claim comparison remains pure and side-effect free. Adding the next supported
  claim field changes the policy dispatcher and focused tests, not the CLI,
  parser, report envelope, or filesystem layer.
- Malformed validation blocks, missing `file`/`function`, a missing batch file,
  and a claim with zero recognized fields must not disappear or pass
  vacuously. Represent them as explicit failed/invalid results and a non-zero
  CLI outcome where applicable. This is a permitted strengthening of a local
  guardrail, not a reason to change successful claim semantics.
- Machine JSON stays on stdout. Human diagnostics go to stderr. Do not add
  timestamped logging, stack traces for expected input errors, a second result
  envelope, or per-module exception classes.
- Focused tests must invoke module APIs and the CLI boundary without importing a
  module that executes `main()`. They use temporary repository roots and
  synthetic source; they never inspect the operator's home directory or real
  credentials.

## Extensibility Seams

Quality's seam is the canonical classifier output map. Adding a quality unit
should extend `.github/quality-path-filters.yaml` and the owning child workflow,
then be proven by the existing ownership checker. It must not require another
changed-file parser or a manually synchronized route enum in every child.

Platform's seam is the stable coordinator plus typed operation calls. Adding the
next deployment operation should add one explicit node and dependency edge,
with a narrow input/secret contract and job-local lifecycle. Adding an
environment should extend one shared logical binding map and the protected
configuration surface; it must not require copy-editing divergent environment
selection rules in every operation.

The trace validator's seam is
`extract facts -> validate claim -> aggregate/serialize`, parameterized by the
existing file/function/optional-class target. Adding a claim such as decorator
or default-value validation belongs in policy over `FunctionInfo`; adding a new
source acquisition mode belongs only at the CLI boundary.

## Gotchas And Anti-Patterns

- Do not replace one large YAML file with generated YAML plus committed
  fragments. That creates two workflow sources of truth and makes review depend
  on generator freshness.
- Do not create one reusable workflow per tiny Quality job. Split by cohesive
  dependency/fan-in ownership; keep coverage with Sonar and matrix producers
  with their consumers.
- Do not make a generic "quality runner" matrix that changes per-package
  dependency/failure isolation merely to reduce lines. The ownership contract
  already supports explicit jobs and matrices; a behavior-changing matrix
  redesign is not part of this refactor.
- Do not duplicate path filters in child workflows or derive deployment routing
  from Quality routing. Quality ownership, deploy selection, portal image
  selection, and stack-smoke selection answer different questions.
- Do not rely on a coordinator's PR gate as the only protection for a
  self-hosted child job. Defense in depth stays at the executing job and in the
  semantic runner-exposure check.
- Do not pass all secrets to every child, use `secrets: inherit`, put secret
  values in reusable-workflow outputs, or use a composite-action input as an
  excuse to expose a protected payload.
- Do not move `GITHUB_ENV` setup into a child and expect a sibling/downstream
  workflow to see it. Use declared non-secret outputs or recreate job-local
  ephemeral files through the canonical renderer.
- Do not turn current hard failures into `continue-on-error`, `soft_fail`,
  `|| true`, warning-only exits, or broad "success or skipped" gates.
- Do not lose artifact/result fan-in when moving Sonar. An aggregate child
  success is not a substitute when the scanner needs an individual producer's
  result and named artifact.
- Do not leave ADR guard reading only the old root files after jobs move. A
  green guard that cannot see the executing child is weakened enforcement.
- Do not broaden ADR exceptions or add a permanent grandfather entry to make
  the new file layout pass.
- Do not turn the trace split into a generic compiler framework, plugin system,
  service/repository stack, Pydantic migration, logging subsystem, or custom
  exception hierarchy.
- Do not silently "fix" top-level-vs-method selection, argument ordering, AST
  nested-scope behavior, or successful JSON output while relocating trace code.
  Any semantic correction needs a named regression test and an explicit
  compatibility decision.

## Non-Goals And Boundaries

- No change to what product code is linted, typechecked, scanned, or tested.
- No change to branch protection, `PR Gate`, docs-only skipping, manual-only
  deployment, protected environments, runner placement, or cloud trust.
- No change to Terraform plan/apply policy, deployment topology, EKS opt-in,
  image build/promotion semantics, provenance, or post-deploy smoke policy.
- No new CI framework, workflow generator, path schema, policy engine, artifact
  store, exception hierarchy, logging platform, or persistence layer.
- No decomposition of `scripts/adr_guard/adr_guard.py`; issue #998 owns that.
  This issue changes only the minimum workflow-graph discovery and invariant
  checks required to keep existing ADR enforcement effective after files move.
- No expansion of the trace language beyond explicit fail-closed input handling
  and the existing extract/validate/batch contract.
- No claim that line-count reduction alone satisfies the issue. Completion
  requires the real nested workflow graph, local guardrail tests, `actionlint`,
  and `python3 scripts/adr_guard/adr_guard.py --all --level ci` to prove the
  same or stronger enforcement.
