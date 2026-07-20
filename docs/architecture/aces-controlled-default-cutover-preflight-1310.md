# ACES Controlled Default Cutover Preflight

Issue: GitHub #1310, "ACES migration: execute controlled default cutover and
rollback selector."

Status: pre-implementation architecture guidance. This note does not change a
catalog route, enable ACES-native provisioning, register or rename a package,
launch or destroy a range, publish cutover evidence, or retire a legacy path.

## Preflight Conclusion

The cutover must not execute from the current checkout. ADR-024 makes parity
evidence a prerequisite, and the repository does not yet contain the reviewed
#1294 Polaris evidence bundle. The parity inventory still names unresolved or
future evidence for multiple cutover surfaces. There is also a lifecycle gap:
`engine.services.destroy_range_by_request()` always dispatches
`engine.ecs.start_range_teardown`, while the ACES-native teardown entrypoint is
`engine.ecs.start_aces_range_teardown`. The #1264 command calls the former
through `cms.services.destroy_range_by_request_id`, so its claim that it tears
down an ACES range is not sufficient until live evidence proves the persisted
ACES plan selects the `aces-range destroy` command.

These are blocking readiness findings, not permission to fold parity or
lifecycle repair into an unreviewed selector flip. #1310 may change the default
only after the owning evidence/fix work is green and reviewed.

## Boundary

ADR-024 remains the cutover doctrine. ADR-031 and ADR-032 now define a more
specific runtime shape than the early migration preflights:

- `SHIFTER_ACES_NATIVE_PROVISIONING` is the default-off **capability gate** for
  the entire ACES-native path. It is not, by itself, a safe selector for which
  source owns a stable public scenario id.
- A registered `AcesPackageSource` keeps its immutable package identity, for
  example `polaris-aces`. It must not be renamed to collide with the legacy
  `polaris` row, inserted through a migration, or exempted from
  `cms.scenarios.legacy_ids`.
- The controlled cutover needs one separate, explicit **catalog source-route
  selector** that maps a stable public id to an existing ACES package-source
  id. The initial route is `polaris=polaris-aces` (or the actual reviewed ACES
  source id in the evidence bundle).
- The empty route set is the rollback value. It restores the legacy `polaris`
  route without deleting, renaming, or rewriting either source.
- ACES routing happens before legacy hydration. The selected ACES package goes
  through `create_range_dispatch -> create_aces_native_range` and the compiled
  `ProvisioningPlan` path. It must not enter `cms.scenarios.hydrator`,
  `RangeSpec`, or `engine.interpreter`. The unselected/rollback route continues
  through the unchanged legacy loader and hydrator. This is the current
  ADR-031/032 meaning of the older “hydrator boundary” wording.

No new ADR is required while implementation stays within those decisions. A
different persistence model, a selector inside ACES SDL/backend manifests, or
ACES-to-`RangeSpec` conversion would change ADR-031/032 and needs an ADR update
before coding.

## Selector Contract

The canonical runtime setting should be
`SHIFTER_ACES_CATALOG_CUTOVERS`, parsed by `config/_aces_settings.py` into an
immutable `public_scenario_id -> aces_package_source_id` mapping. Its default is
empty. The environment form is a comma-separated list of strict slug pairs,
for example:

```text
SHIFTER_ACES_CATALOG_CUTOVERS=polaris=polaris-aces
```

This is a non-secret deployment setting. It is the only new selection seam;
do not add per-view flags, a Polaris boolean, a database “default” bit, a YAML
marker, or a second route table.

The selector must satisfy these invariants:

- Strictly validate one `=` per pair, non-empty Django-compatible slugs,
  bounded lengths, and unique public and target ids. Invalid or duplicate
  input raises `django.core.exceptions.ImproperlyConfigured`; it is never
  silently ignored.
- A non-empty mapping requires `ACES_NATIVE_PROVISIONING_ENABLED`. An invalid
  two-key posture fails startup/readiness rather than silently falling back to
  legacy. Rollback clears the route mapping; it need not disable the parallel
  ACES capability or its review/validation surfaces.
- Before activation, every target must resolve to exactly one existing
  `AcesPackageSource`, pass `validate_package_source`, name a supported
  source/contract/profile, have `conformance_status=passed`, satisfy package
  and lock identity checks, and be realizable by the published
  `provisioning-only` backend manifest. Missing or stale evidence fails closed;
  an active route never falls back to legacy on an ACES error.
- Every public id must resolve to an existing legacy YAML/default or active DB
  scenario so the empty mapping is a real rollback route, not a promised one.
- The unified catalog emits the selected public id exactly once. While selected,
  `polaris` is the ACES-backed entry and the internal target id is not a second
  launch choice. With the mapping empty, `polaris` is the legacy entry and the
  distinct ACES source may remain visible only under its existing review/test
  posture.
- `ScenarioMetadata` remains keyed by the stable public id. Existing
  `enabled` and `staff_only` policy therefore survives cutover and rollback;
  the package row must not duplicate access fields.
- Range, CTF, Mission Control, audit, and status correlation keep the public id
  and `request_id`. Package identity remains the package-source id plus
  ref/version/digest/profile in the existing bounded ACES evidence surfaces.

The registry should own resolution. Product callers continue to consume
`cms.services.list_launchable_scenarios` and `create_range_dispatch`; they must
not read the setting, query `AcesPackageSource`, or implement aliasing
themselves. `create_range_dispatch` must use the same resolved route as the
catalog and launchability checks, not `_is_aces_scenario()` as a second routing
decision.

## Required Reuse

| Concern | Canonical incumbent | Cutover guardrail |
| --- | --- | --- |
| Migration and runtime doctrine | ADR-024, ADR-031, ADR-032; `aces-cutover-archive-plan-preflight-1238.md` | Keep capability enablement, source routing, and archive cleanup as distinct concepts. |
| Catalog and access | `cms.scenarios.registry`, `ScenarioWorkflow`, `ScenarioMetadata`, `cms.services.list_launchable_scenarios` | Resolve the route once and preserve workflow-aware launchability plus the public access overlay. |
| Package identity and registration | `cms.services.register_pack`, `AcesPackageSource`, `PackageSourceRecord`, `validate_package_source`, `cms.scenarios.legacy_ids` | Route to the already-registered distinct source id; do not rename, raw-update, seed, or exempt a colliding row. |
| Package content safety | `cms.scenarios.pack_validation`, `shared.aces.package_loader`, `shared.aces.object_source` | Preserve upstream pack validation, root containment, archive bounds, immutable digest binding, object-key policy, and launch-time re-verification. |
| ACES admission | `shared.aces.manifest`, `shared.aces.runtime_target`, ACES-owned conformance tests | The manifest is capability evidence, not routing configuration. A route may select only an already-supported profile. |
| Legacy rollback | `cms.scenarios.loader`, `cms.scenarios.hydrator`, `cms.scenarios/templates/polaris.yaml`, `create_range` | Leave loader slug/path/`safe_load`/Pydantic gates and the legacy create body unchanged. |
| ACES launch | `create_range_dispatch`, `create_aces_native_range`, `CmsAcesDispatchPort`, `engine.services.create_aces_range` | Route before hydration and retain user, active-range, backend-admission, reservation, audit, and failure-status controls. |
| Persistence | CMS `Request`/`RangeInstance`; engine `Request`/`Range.range_config`; ACES operation sidecars | Keep `range_spec=None` for ACES and the serialized ACES plan as the existing versioned engine payload. Add no selector table or duplicate runtime schema. |
| Lifecycle and status | persisted `range_config.kind`, `RangeEventOutbox`, `apply_range_status`, `reconcile_range_events`, `ResourceStatus` | Provision/destroy selection for an existing range derives from persisted kind, never the current catalog selector. Keep one lifecycle/status pipeline. |
| Product boundaries | `ctf.bridges`, `ctf.services.range.*`, Mission Control views/APIs, terminal/Guacamole services | CTF and Mission Control keep calling CMS/engine service facades and retain current ownership, scope, participant, and capacity checks. |
| Errors | `CMSError`, `shared.errors`, `shared.api.errors`, existing view classifiers | Translate once at service/API boundaries; add no cutover or ACES exception hierarchy. |
| Logging and audit | `shared.log_sanitize`, provisioner `log_redact`, `shared.audit` / `risk_register` adapter | Record sanitized route ids, request ids, digests, status classes, and evidence refs. Registration and any mutable catalog action retain strict audit behavior. |
| Runtime config | `config/_aces_settings.py`, generated `config/env-manifest.json`, `installation.runtime_inventory`, AWS deploy env assembly, GCP runtime env/renderer and Helm ConfigMap | Deliver the same validated non-secret selector to every portal/CMS/CTF process; do not rely on a shell-only override. |
| Repository enforcement | `.importlinter`, `scripts/check_layer_imports/layer_imports.yaml`, `scripts/adr_guard`, stack-native tests | Do not weaken import, conformance, secret, workflow, Terraform, Kubernetes, smoke, or ADR gates. |

## Cross-Cutting Layers

### Security and validation

- **Authentication and authorization:** catalog review/registration remains
  behind `threat_research_required`, `validate_cms_authoring_user`,
  `HasCMSAuthoringActor`, and exact CMS authoring scopes. Mission Control keeps
  `IsAuthenticatedSessionOrApiToken`, `HasMissionControlActor`, exact
  `mission_control:*` scopes, owner checks, and participant blockers. CTF keeps
  organizer/participant service checks. The selector is deployment config, not
  an HTTP field or user preference.
- **Selector/config shape:** strict parsing lives in `config/_aces_settings.py`.
  The generated env manifest, runtime inventory, provider renderers, deploy
  tests, and settings tests must agree on the key and default. All web and
  worker roles must see one value; a rolling deployment with mixed route maps
  is an invalid cutover posture because listing and asynchronous launch could
  disagree.
- **Legacy input shape:** rollback still passes slug validation, template-path
  containment, `yaml.safe_load`, and `TypeAdapter(AnyScenarioTemplate)` in the
  legacy loader/model boundaries.
- **Package/ACES shape:** the selected row still passes
  `PackageSourceRecord`/`validate_package_source`, canonical pack validation,
  package-root or object-archive containment and bounds, digest re-verification,
  the supported contract/profile allowlists, ACES parser/semantic validation,
  `shared.aces.runtime_target` admission, and the published backend manifest
  conformance gate. Selection is not conformance.
- **Backend policy:** both legacy and ACES creates retain
  `_assert_live_fire_backend_admitted`; the trusted backend admission result is
  carried into engine persistence. A GCE-only ACES realization cannot become a
  global AWS/default claim. Each tenant/provider named in the cutover record
  needs its own green live evidence.
- **Secret handling:** the selector contains slugs only. It must not carry
  package bodies, storage URLs, credentials, flags, provider ids, secret refs,
  or rendered config. Logs, audit JSON, APIs, events, DLQs, evidence, docs, and
  workflow summaries retain the existing allowlisted/redacted projections.
- **OS/process exposure:** the selector is environment config shared by Django
  roles, never a user-supplied CLI argument. Provisioner dispatch remains
  structured argv containing `aces-range provision|destroy --request-id`; no
  package/YAML content, token, provider payload, or shell fragment enters argv.
- **Error envelopes:** startup/config errors use `ImproperlyConfigured` and
  name only the invalid key/id. Service failures use `CMSError`; DRF uses
  `shared.api.errors`; HTML flows use current safe classifiers. Raw parser,
  storage, Terraform, cloud, SSH/SSM, CTFd, or provider exceptions remain out
  of client responses and the cutover record.

### Reliability, persistence, and observability

- Source selection is evaluated before any range reservation. Once reserved,
  the persisted engine `range_config.kind` is the lifecycle discriminator.
  Selector rollback affects new launches only; existing ACES ranges must still
  be queryable, reach READY/FAILED, and dispatch `aces-range destroy` while the
  selector is empty.
- Do not persist a copy of the route mapping in `Scenario.definition`,
  `RangeInstance.range_spec`, `Range.range_config`, sidecar payloads, events, or
  audit JSON. Persist the already-canonical public scenario id, request id, and
  ACES plan/evidence identities at their existing owners.
- A selector change is observable as sanitized old/new route ids, deployment
  revision, environment/provider, readiness verdict, and evidence refs. Do not
  log an entire environment, provenance object, package body, provider output,
  terminal stream, command string, credential, token, or flag.
- The cutover health gate must prove every serving/worker replica reports the
  same selector fingerprint and that catalog resolution, launch, status,
  destroy, and rollback resolve consistently. A process-local cache that can
  outlive a deploy or database/source update is prohibited.

## Extensibility Seam

The mapping is deliberately parameterized by stable public id and registered
package-source id. The next scenario cutover adds another pair; it does not add
a boolean, model field, core-service branch, or CTF/Mission Control change.
Another ACES source kind/profile still extends the existing package-source,
registry allowlists, loader/adapter, manifest, and conformance seams before it
can become a route target.

Provider selection is orthogonal. Do not add provider to the route key or put
provider rules in the catalog. Deployment-specific config selects routes only
after the existing backend admission and manifest evidence is green for that
environment.

## Reviewed Cutover Record

The acceptance record is an operational review artifact, not a new runtime
schema. One reviewed record must name:

- repository revision, deployment/environment/provider, timestamp, reviewer,
  and the exact selector key plus old/new sanitized route values;
- public id, selected package-source id, contract/profile, immutable
  package/lock digests, backend-manifest identity, and conformance report ref;
- parity-inventory review and the #1294 redacted evidence-bundle ref, including
  normal portal/CMS/engine/provisioner launch, Polaris infrastructure and
  scenario-verification results, CTFd readback, Mission Control/CTF projection,
  lifecycle destroy, and full ADR/stack checks;
- rollback value (empty mapping), the preserved legacy loader/template path,
  replica restart/readiness expectations, and proof that rollback launches the
  legacy `polaris` path while an existing ACES range remains destroyable;
- the rollback window: at least the one proven release required by ADR-024,
  extended if any live ACES range or retained plan still needs ACES lifecycle
  support; and
- stale docs/issues/tests proposed for #1311/#1312 retirement after the window,
  without deleting them in #1310.

The record carries refs, ids, digests, counts, statuses, and sanitized reasons
only. It must not copy raw evidence, flags, credentials, terminal/Guacamole
URLs, CTFd tokens, presigned URLs, package bodies, commands, Terraform/cloud
outputs, or environment values.

## Whole-Repository Scope

The later implementation must evaluate all of these owners, even if the final
diff is smaller:

- ADR-024, ADR-031, ADR-032 in `docs/architecture/aces-migration-adr.md` and
  `docs/adr/index.yaml`;
- `docs/architecture/aces-cutover-archive-plan-preflight-1238.md`,
  `aces-polaris-acceptance-parity-gate-preflight-1237.md`,
  `aces-cutover-evidence-1264.md`, and
  `aces-migration-parity-inventory.yaml`;
- `config/_aces_settings.py`, `config/settings.py`, generated
  `config/env-manifest.json`, config tests, `installation/runtime_inventory.py`,
  AWS deploy/runtime env assembly, GCP runtime env renderer/inventory, and Helm
  ConfigMap consumers;
- `cms/scenarios/{registry,legacy_ids,loader,pack_validation}.py`,
  `cms/models/scenarios.py`, `cms/services/{_content_ingestion,_scenarios,
  _aces_range_create,_range_create,_range_create_validation,
  _range_destroy}.py`, and catalog/editor/API presentation;
- `shared/schemas/aces_package_source.py`, `shared/aces/{package_loader,
  object_source,manifest,runtime_target,dispatch_port,projections}.py`, and
  shared error/log/audit helpers;
- `engine/services/{_aces_range,_range,_range_by_request}.py`, `engine/ecs`,
  persisted `Range.range_config`, provisioner `main.py`, `aces_plan.py`, and
  `aces_range_ops.py`;
- Mission Control launch/list/history/lifecycle APIs and views, CTF bridges and
  range services, `RangeEventOutbox`, status handlers/reconciler, ACES sidecar
  projections, terminal/Guacamole access, and image registry;
- `scenario-dev/polaris/**`, the installed scenario-verification plugin report,
  `scripts/polaris-aws-range/**`, and `scripts/ctfd-workshop/**` as evidence,
  not routing logic; and
- `.importlinter`, `scripts/check_layer_imports/layer_imports.yaml`,
  `scripts/adr_guard/**`, `.github/quality-path-filters.yaml`, and all
  path-triggered stack checks.

## Gotchas And Anti-Patterns

- Do not use `SHIFTER_ACES_NATIVE_PROVISIONING` alone as source precedence. It
  enables the parallel capability and several management surfaces; it does not
  identify which public id should stop resolving to legacy.
- Do not rename `polaris-aces`, change an authored pack identity, allow a
  cross-store `scenario_id` collision, or raw-update a row to make the public
  id look reclaimed. Route the stable public id to the immutable source id.
- Do not silently fall back to legacy while an ACES route is active. That hides
  failed conformance and produces environment-dependent behavior under one id.
- Do not route independently in catalog serializers, CTF forms, Mission Control
  views, `create_range_dispatch`, or provisioner code. One registry resolution
  must feed projection and launch.
- Do not treat the selector as an ACES manifest capability, package field,
  launchability bit, access flag, backend/provider choice, or scenario DSL term.
- Do not send an ACES package through the legacy hydrator or wrap it in
  `RangeSpec`; do not send legacy YAML into the ACES parser.
- Do not choose destroy/provision behavior from the current selector or public
  id. In-flight lifecycle dispatch comes from persisted validated kind.
- Do not accept a rolling mixed-selector fleet, cache a route past deployment
  convergence, or declare rollback proven by a unit test that never invokes
  the real `aces-range destroy` command.
- Do not post a cutover record before the evidence exists, mark inventory rows
  reconciled by prose alone, or weaken conformance, scopes, secret scanning,
  import checks, ADR guard, smoke, CTFd readback, Terraform, Kubernetes, or
  workflow gates.

## Non-Goals And Implementation Boundaries

- No selector/config/code/model/migration/API/UI implementation in this
  preflight; no package registration or identity change; no live cutover or
  rollback operation.
- No archive or removal of the legacy Polaris template, CyberScript contracts,
  standalone Polaris evidence, docs, issues, or tests. Those remain #1311/#1312
  work after the rollback window.
- No new scenario schema, package schema, launchability model, exception
  hierarchy, event family, status enum, evidence store, audit store, config
  framework, or provider abstraction.
- No expansion of the `provisioning-only` backend claim to orchestrator,
  evaluator, participant-runtime, or observation protocols.
- No assumption that a GCE proof authorizes AWS or another provider cutover.
- No new Ground Control requirement UID for this requirement-free run.

## Validation Expectations

This architecture note requires:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```

The later cutover implementation must also run every stack-native and
provider/evidence check required by `AGENTS.md`, ADR-024, and the changed-path
quality contract. Passing unit tests without reviewed live evidence is not a
cutover gate.
