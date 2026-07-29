# ACES Total Default Cutover Preflight

Issue: GitHub #1310, "ACES migration: total default cutover to ACES-native
path (temporary rollback switch)."

Status: pre-implementation architecture guidance. This note does not implement
the selector, change a runtime default, register or promote a pack, launch or
destroy a range, deploy a tenant, or publish the required cutover record.

## Preflight Conclusion

#1310 is an authorized default cutover, not another default-off experiment. The
shipped application and provider configuration must select the ACES-backed
`polaris`; an explicitly empty route plus the disabled native capability remain
only as the temporary rollback posture.

Four repository facts are on the critical path:

1. `engine.services.destroy_range_by_request()` always calls the legacy
   `engine.ecs.start_range_teardown`, although
   `engine.ecs.start_aces_range_teardown` exists. Existing-range lifecycle must
   dispatch from the persisted, validated `Range.range_config.kind`.
2. `cms.scenarios.registry` suppresses every `AcesPackageSource` whose
   `scenario_id` collides with a legacy id, while
   `create_range_dispatch()` independently queries `AcesPackageSource`. The
   registry does not yet own one source-resolution result.
3. The data model and ingestion service currently conflate three identities:
   the stable public launch id (`polaris`), the internal package-source id
   (for example `polaris-aces`), and the authored pack identity
   (`pack.yaml.name == polaris`). `register_pack()` requires its `scenario_id`
   to equal the authored name and the no-shadow guard rejects `polaris`, so the
   required distinct internal source cannot currently be registered through
   the canonical boundary.
4. The referenced `../shifter-scenarios-panw/polaris` pack is not currently
   ingestible by Shifter's pinned pack contract: it is marked `draft`, has no
   `associated_artifact_manifest`, and contains two direct `sdl/*.sdl.yaml`
   files while `shared.aces.package_loader` requires exactly one launch entry.
   Shifter's shipped in-box manifest is also empty.

The real Polaris pack and its immutable deploy-time staging/registration are
therefore a prerequisite to activating the default, not an optional follow-up.
The dedicated AWS realization follow-up may leave ACES range creation
non-functional on AWS, as #1310 permits, but AWS still must receive the same
selector and capability posture across its portal and workers. ADR-024's
parity, conformance, normal-path lifecycle, rollback, and repository gates
remain blocking evidence.

## Architecture Decisions And Boundaries

ADR-024 remains the cutover doctrine; ADR-031/032 own the runtime shape.
ADR-031 is updated with this preflight to record the authorized phase change
from default-off parallel development to ACES-native default operation.

- `SHIFTER_ACES_NATIVE_PROVISIONING` is the temporary capability/rollback
  gate. Its shipped default becomes true. It is not source precedence.
- One catalog source-route setting, `SHIFTER_ACES_CATALOG_CUTOVERS`, maps a
  stable public id to an internal registered source id. Its shipped default is
  `polaris=polaris-aces`, where `polaris-aces` is the reviewed internal id used
  when the real pack is registered.
- An explicitly empty route set restores legacy source precedence. A complete
  rollback empties the route before disabling the native flag. A non-empty
  route with the capability disabled is invalid configuration and fails
  closed.
- `cms.scenarios.registry` owns resolution. Catalog listing, launchability, and
  `create_range_dispatch` consume the same resolved entry; the dispatcher
  removes its second `_is_aces_scenario()` decision.
- While selected, the catalog publishes `polaris` exactly once and the
  internal id is not offered as another launch choice. `ScenarioMetadata`
  remains keyed by public id, preserving its existing access policy.
- ACES selection occurs before legacy hydration. ACES continues through the
  compiled `ProvisioningPlan` path with `RangeInstance.range_spec=None`;
  rollback continues through the existing CyberScript loader, Pydantic
  template validation, `RangeSpec`, interpreter, and provisioner path.
- New launches use current registry resolution. Status and teardown for an
  existing range use the persisted plan discriminator and never consult the
  current selector or capability flag.

The selector is a launch-routing seam only. It is not an access-control flag,
package attribute, conformance verdict, provider selector, backend capability,
scenario DSL field, or lifecycle discriminator.

## Identity Contract And Polaris Prerequisite

The implementation must separate, not rename or alias implicitly, these
concepts:

| Concept | Owner | Cutover value |
| --- | --- | --- |
| Public launch identity | Registry projection, `ScenarioMetadata`, product workflows | `polaris` |
| Internal source identity | Existing `AcesPackageSource` registry reference | `polaris-aces` |
| Authored package identity | Pinned `aces-scenario-packs` validation of `pack.yaml` | `polaris` |

Evolve the existing `AcesPackageSource` / `PackRegistrationRequest` contract
only enough to hold and validate the internal source identity separately from
the authored package identity. Do not add an alias table, a second package
schema, a Polaris model, or a privileged registration path. The authored
identity remains established by `cms.scenarios.pack_validation`; the internal
id remains a bounded slug; the full immutable identity remains the existing
source kind, package ref/version/digest, lock identity, contract/profile, and
conformance evidence. Preserve no-shadow enforcement for stored source ids.

The real pack must first become a proper, immutable, Shifter-ingestible ACES
package:

- one explicit direct SDL launch entry, selected by the pack contract rather
  than path sniffing or a scenario-name branch;
- an `associated_artifact_manifest` and canonical digest covering its SDL,
  content packages, contract module, and other required associated artifacts;
- a passed conformance report for the supported `aces/shifter` profile; and
- immutable availability under the deployed `ACES_PACKAGE_ROOT` or the
  existing object-source resolver.

Use the existing `cms.scenarios.inbox` manifest and
`bootstrap_inbox_catalog` path for a shipped pack. That path deliberately calls
the same authorized, validated, audited `register_pack` service as operator
ingestion and is transactionally idempotent. Do not seed the row with a data
migration, fixture, direct ORM write, or deployment-only bypass. The route may
be activated only after bootstrap and conformance promotion have made the
target resolvable. Syntax can be checked at settings import; database/package
readiness belongs at bootstrap/readiness and registry resolution, never at
module import or migration discovery.

## Selector Contract

`config/_aces_settings.py` is the canonical parser. Parse the environment form
into an immutable `public_id -> source_id` mapping. The comma-separated form is:

```text
SHIFTER_ACES_CATALOG_CUTOVERS=polaris=polaris-aces
```

It must enforce:

- exactly one `=` per pair; bounded, non-empty Django-compatible slugs; unique
  public ids and unique target ids; no ignored entries or last-wins behavior;
- a strict boolean for `SHIFTER_ACES_NATIVE_PROVISIONING`; unrecognized values
  must not become false silently;
- `ImproperlyConfigured` for malformed syntax or the invalid
  non-empty-route/native-disabled combination, without echoing arbitrary input;
- a target that resolves to exactly one registered source and passes the
  existing source-kind, contract/profile, conformance, reference, digest,
  package, lock, and backend-realizability gates;
- a public id with a preserved legacy source, so empty routing is a genuine
  rollback; and
- no fallback to legacy after an active ACES route encounters an ACES
  validation, compilation, dispatch, or provision failure.

Both application defaults and every deployment environment must state the
ACES posture explicitly. Provider configuration must not rely on an image
fallback, because mixed old/new processes could otherwise list one source and
dispatch another. A rollout is ready only when all portal, CMS, Mission
Control, CTF, scheduler, and worker replicas have the same sanitized selector
fingerprint and native flag.

## Required Cross-Cutting Reuse

| Concern | Canonical incumbent | Guardrail |
| --- | --- | --- |
| Doctrine | ADR-024, ADR-031, ADR-032; #1238 preflight and parity inventory | Keep source routing, native capability, lifecycle, and archive as separate concepts. |
| Catalog/access | `cms.scenarios.registry`, `ScenarioWorkflow`, `ScenarioMetadata`, `cms.services.list_launchable_scenarios` | Resolve once; preserve workflow filtering and public-id access overlays. |
| Ingestion/identity | `cms.services.register_pack`, `PackRegistrationRequest`, `AcesPackageSource`, `cms.scenarios.inbox`, `bootstrap_inbox_catalog`, `cms.scenarios.legacy_ids` | Extend the existing reference contract; retain one uniform authorized and audited ingestion path. |
| Source schema | `shared.schemas.aces_package_source.PackageSourceRecord` and `validate_package_source` | Preserve the bounded provenance-only record; do not persist bodies, runtime config, or credentials. |
| Pack safety | `cms.scenarios.pack_validation`, `shared.aces.package_loader`, `shared.aces.object_source` | Reuse upstream contract validation, canonical digest, containment, archive bounds, and launch-time verification. |
| ACES admission | Registry allowlists, `shared.aces.manifest`, `shared.aces.runtime_target`, ACES conformance | Selection never substitutes for conformance or backend realizability. |
| Legacy path | `cms.scenarios.loader`, schema adapters/hydrator, `create_range` | Leave slug/path containment, `yaml.safe_load`, Pydantic validation, and legacy create behavior intact. |
| Launch | `create_range_dispatch`, `create_aces_native_range`, `CmsAcesDispatchPort`, `engine.services.create_aces_range` | Preserve ownership, active-range, backend admission, transaction/reservation, audit, and failure status. |
| Persistence | CMS `Request`/`RangeInstance`; engine `Request`/`Range.range_config`; ACES sidecars | Keep the serialized versioned plan as the ACES payload; add no selector snapshot or duplicate runtime schema. |
| Lifecycle/status | persisted `range_config.kind`, `start_aces_range_teardown`, `RangeEventOutbox`, `apply_range_status`, reconciler | Select teardown from persisted validated kind and reuse one status pipeline. |
| Product workflow | Mission Control views/APIs and `ctf.bridges` / range services | Continue through CMS service facades with current scopes, ownership, participant, and capacity checks. |
| Errors | `CMSError`, `shared.errors`, `shared.api.errors`, existing HTML classifiers | Translate once at boundaries; add no cutover exception hierarchy. |
| Logging/audit | `shared.log_sanitize`, provisioner `log_redact`, `shared.audit` | Emit bounded ids/fingerprints/statuses and strict registration audit; never dump environment or payloads. |
| Settings/deploy | `_aces_settings.py`, `env-manifest.json`, `shifter/installation/runtime_inventory.py`, provider renderers | One validated key/value contract must reach every relevant process. |
| Enforcement | `.importlinter`, layer checks, `scripts/adr_guard`, stack-native checks | Keep ACES imports inside `shared.aces` and do not weaken existing gates. |

## Cross-Cutting Layers The Design Must Pass

### Security, validation, and error surfaces

- **HTTP authorization:** registration/review remains behind
  `validate_cms_authoring_user`, `threat_research_required`,
  `HasCMSAuthoringActor`, and exact authoring scopes. Mission Control retains
  `IsAuthenticatedSessionOrApiToken`, `HasMissionControlActor`, write scopes,
  owner checks, participant blockers, and throttles. CTF retains organizer and
  participant service checks. The selector is deployment config and is never
  accepted from an HTTP request, user preference, scenario editor, or SDL.
- **Settings shape:** `_aces_settings.py` validates the strict boolean and
  route grammar and raises `ImproperlyConfigured`. Literal environment
  bindings or `_EXPLICIT_BINDINGS` keep generated `config/env-manifest.json`
  complete. `config/_posture.py` may report only a safe enabled state and route
  fingerprint/count.
- **AWS configuration:** Terraform variables, module inputs, SSM parameters,
  environment `tfvars`, `portal/ec2/user_data.sh`, and
  `scripts/portal-deploy/deploy_portal.sh` must validate the same boolean and
  slug-pair grammar before constructing Docker arguments. Both bootstrap and
  redeploy paths must pass both values to the portal and every CMS/engine/MC/
  CTF/maintenance worker.
- **GCP configuration:** `scripts/gcp/render_runtime_env.py`,
  `shifter/installation/runtime_inventory.py`, the generated
  `platform-runtime` ConfigMap/Kustomize overlay, and Helm `runtimeEnv` must
  agree on ownership and values. The web, CMS, engine, MC, CTF scheduler,
  launcher, reconciler, outbox, and prune deployments consume the same
  ConfigMap. Do not put these non-secrets in the Secret or forward them as
  provisioner-job runtime inputs.
- **Legacy parser:** rollback continues through slug validation, template-root
  containment, `yaml.safe_load`, and `TypeAdapter(AnyScenarioTemplate)`.
- **Package parser:** registration and launch continue through the pinned pack
  validator, `PackageSourceRecord`, repo-root/object-key containment,
  download/extraction bounds, canonical digest verification, exactly-one-entry
  enforcement, and supported source/contract/profile allowlists.
- **ACES/provisioner parser:** compilation remains inside `shared.aces`.
  `aces_plan.parse_plan` validates the persisted discriminator, contract and
  producer versions, resources, payloads, and topology before cloud mutation.
  Backend admission and the published manifest remain required.
- **Secret and OS exposure:** selectors contain slugs only. GCP ConfigMaps and
  AWS SSM parameters are non-secret. AWS Docker `-e NAME=value` may expose
  these non-secret values in host process/container metadata; strict grammar
  prevents shell content. Credentials and package bodies remain secret
  references or contained files and never enter the selector, logs, evidence,
  APIs, or argv. Provisioner argv remains the structured
  `aces-range provision|destroy --request-id <id>` form.
- **Error envelopes:** configuration failures name only the key/constraint;
  service failures use `CMSError`; DRF and HTML use their existing safe
  classifiers. Raw YAML/pack/parser, object storage, Terraform, cloud, SSM/SSH,
  CTFd, or provider exceptions do not reach clients or the cutover record.

### Persistence, reliability, and observability

- Resolve before reservation. After reservation, persist only the existing
  public scenario/request identities and the canonical legacy or ACES payload.
  Do not copy the selector into `Scenario.definition`,
  `RangeInstance.range_spec`, `Range.range_config`, events, or sidecars.
- Teardown must parse `range_config` fail-closed and dispatch legacy or ACES
  teardown from its known `kind`. Unknown/malformed kinds fail without cloud
  mutation. Preserve the existing status rollback when task dispatch fails.
- Disabling the flag or clearing the route affects only future catalog
  selection. It must not make existing ACES ranges unqueryable or
  undestroyable.
- Emit sanitized startup/readiness posture, source resolution, request id,
  public/source ids, digest/report refs, lifecycle kind, and status class at
  their current logging/audit owners. Do not add a parallel telemetry stream or
  log full settings, provenance, plans, manifests, commands, cloud output, or
  environment values.
- Do not retain a process-local route cache across deployment/config or source
  changes. Readiness and the reviewed cutover record must demonstrate a
  fleet-uniform fingerprint.

## Extensibility Seam

The only future-facing seam is the validated
`public_scenario_id -> package_source_id` mapping at the registry boundary.
The next scenario cutover adds one pair and one conformant source; it does not
add a boolean, provider dimension, model flag, view branch, or CTF/Mission
Control change. New source kinds or contract profiles first extend the existing
source validator, resolver, registry allowlists, adapter/manifest, and
conformance evidence. Provider admission remains orthogonal to catalog routing.

The pack's one launch entry should use the upstream package contract's explicit
entry/variant seam. If the pinned contract cannot express the intended Polaris
entry, that contract capability is the dependency to resolve; Shifter must not
compensate with filename order, YAML-shape detection, or a Polaris branch.

## Reviewed Cutover And Rollback Evidence

The required cutover record is an operational review artifact, not a runtime
model. It must identify repository/deployment revision, environment/provider,
sanitized selector transition, public/source/package identities, immutable
digests, conformance and backend-manifest refs, parity-inventory reconciliation,
and evidence for the normal portal -> CMS -> engine -> provisioner launch,
status, and destroy path.

Rollback evidence must launch an ACES range first, empty the route and disable
the native launch capability fleet-wide, prove a new `polaris` launch resolves
to legacy, and destroy the already-existing ACES range through
`aces-range destroy`. A unit test or selector flip without the persisted-kind
destroy is not rollback proof. Keep the rollback line for at least the
ADR-024-required release and while any retained ACES plan needs lifecycle
support.

The record contains refs, ids, digests, counts, status classes, and bounded
reasons only. It does not copy flags, secrets, terminal/Guacamole URLs, tokens,
presigned URLs, package bodies, plans, commands, Terraform/cloud outputs, or
raw environment values.

## Whole-Repository Scope

The implementation must evaluate these owners even where no edit is necessary:

- `docs/architecture/aces-migration-adr.md`,
  `aces-cutover-archive-plan-preflight-1238.md`,
  `aces-migration-parity-inventory.yaml`, and `docs/adr/index.yaml`;
- `config/_aces_settings.py`, `config/settings.py`,
  `config/env-manifest.json`, `config/_posture.py`, and settings tests;
- `cms/scenarios/{registry,legacy_ids,inbox,pack_validation}.py`,
  `cms/scenarios/inbox_packs/manifest.yaml`, `cms/models/scenarios.py`,
  `cms/services/{_content_ingestion,_aces_range_create,_range_create,
  _range_create_validation}.py`, and catalog presentation/API code;
- `shared/schemas/aces_package_source.py`,
  `shared/aces/{package_loader,object_source,manifest,runtime_target,
  dispatch_port}.py`, and shared auth/error/log/audit helpers;
- `engine/services/{_aces_range,_range_by_request}.py`, `engine/ecs`,
  `engine.models.Range.range_config`, provisioner `main.py`, `aces_plan.py`,
  `aces_range_ops.py`, status/outbox handlers, and reconciler;
- Mission Control launch/list/status/destroy views and APIs, CTF bridges/range
  services, terminal/Guacamole access boundaries, and capacity admission;
- `shifter/installation/runtime_inventory.py`,
  `scripts/gcp/render_runtime_env.py`, GCP generated/static runtime env inputs,
  base/overlay workload manifests, and the Helm runtime ConfigMap/workloads;
- `platform/terraform/modules/portal/ssm/{variables,main}.tf`,
  `platform/terraform/modules/portal/ec2/user_data.sh`,
  `platform/terraform/environments/{dev,proof,prod}/portal/{variables,main}.tf`
  and `terraform.tfvars`, plus `scripts/portal-deploy/deploy_portal.sh`;
- the real `../shifter-scenarios-panw/polaris` package as the upstream content
  dependency and `scenario-dev/polaris/**` only as retained legacy/evidence,
  never core routing logic; and
- `.importlinter`, `scripts/check_layer_imports/layer_imports.yaml`,
  `scripts/adr_guard/**`, changed-path workflow filters, Terraform, Kubernetes,
  conformance, smoke, and lifecycle tests.

## Gotchas And Anti-Patterns

- Do not ship default-off settings, a dormant selector, or rely only on
  framework defaults instead of explicit AWS/GCP deployment values.
- Do not use the native flag alone as source precedence or let the selector
  bypass conformance/backend admission.
- Do not rename the authored `polaris` pack to `polaris-aces`, weaken its
  identity validation, allow a stored cross-source collision, or insert it
  directly. Separate public, source, and package identity at their owners.
- Do not expose the internal source as a second launch choice.
- Do not route independently in serializers, views, CTF, Mission Control,
  `create_range_dispatch`, engine, or provisioner. One registry result feeds
  projection and dispatch.
- Do not infer routing from YAML shape, source existence, filename, provider,
  public id, or a Polaris-specific branch. An active ACES failure is not a
  reason to fall back to legacy.
- Do not send ACES through `RangeSpec`/CyberScript hydration or legacy YAML
  through the ACES compiler.
- Do not choose lifecycle from the current selector, flag, public id, or
  package-source row. Use persisted validated kind.
- Do not make the two config values secret, put package/credential data into
  them, forward them into provisioner jobs, or render unvalidated values into
  shell command strings.
- Do not accept a mixed fleet, a partial inbox bootstrap, a mutable package
  ref, multiple implicit SDL entries, or a route activated before conformance.
- Do not mark parity rows reconciled by prose, treat generic ACES validation as
  real Polaris evidence, or weaken ADR/import/security/provider gates.

## Non-Goals And Implementation Boundaries

- This preflight contains no selector/config/model/migration/deploy
  implementation, package registration, conformance promotion, live cutover,
  rollback, or evidence publication.
- #1310 does not archive or remove legacy Polaris, CyberScript contracts,
  docs, tests, or evidence. Those remain #1311/#1312 after the rollback window.
- #1310 need not make AWS ACES realization functional, but it must make AWS
  configuration delivery uniform and must not claim a successful AWS launch.
- Do not add a new authored scenario schema, provisioning schema, alias table,
  launchability model, exception hierarchy, event/status family, evidence
  store, audit store, config framework, or provider abstraction. A minimal
  distinction inside the existing package-source/registration contract is in
  scope because the current identity conflation makes the required route
  impossible.
- Do not expand the `provisioning-only` backend claim to orchestration,
  evaluation, participant-runtime, or observation protocols.
- Do not retire the temporary rollback controls in this issue.
- No Ground Control requirement UID is created for this requirement-free run.

## Validation Expectations

This architecture change requires:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```

The later cutover implementation must also pass every stack-native,
changed-path, provider, conformance, normal-path lifecycle, rollback, and live
evidence gate required by `AGENTS.md` and ADR-024.
