# Uniform Content Ingestion Preflight

Issue: GitHub #1578, "Uniform, entitlement-blind content ingestion (pack =
universal unit)."

Status: pre-implementation architecture guidance. This note adds no runtime
behavior and is governed by ADR-034.

## Boundary

One registration operation accepts a pack that is already in the operator's
possession. Its input must not vary with whether the pack shipped in-box, came
from a public or private source, or was authored locally. Acquisition,
licensing, purchase, subscription, operator identity at acquisition, and
private-source credentials are outside this operation.

This is deliberately different from *trust* and from *resolution*. Signature
or digest verification, ACES conformance, backend realizability, and an
artifact reference are content-safety/capability facts. `source_kind` (`repo`
or `object`) is a resolver/storage fact. Neither is a provenance or entitlement
field and neither may select a different registration workflow.

## Decisions And Required Reuse

- Put registration behind one public CMS service boundary, then have every
  caller use it: an authenticated authoring endpoint, an operator tool if one
  is added, and the in-box bootstrap/seed path. There is no current generic
  registration service to reuse, so this small service seam is warranted; do
  not put the transaction, validation, audit, or duplicate handling in views,
  management commands, fixtures, or startup code.
- Build the catalog projection on `cms.scenarios.registry`; preserve
  `ScenarioMetadata` as the sole `enabled` / `staff_only` overlay and preserve
  the registry as the sole launchability authority. Registration is not
  launchability, and a registered pack may remain review-only or
  non-realizable.
- Reuse `shared.schemas.aces_package_source.PackageSourceRecord` and
  `validate_package_source()` for the current ACES/Shifter package-source
  record, including bounded reference-only provenance. Do not store package
  bodies, imports, generated content, flags, credentials, presigned URLs, or
  runtime configuration in `AcesPackageSource`, `Scenario.definition`, audit
  JSON, or a new registration table.
- The in-box catalog must be declared as registration inputs and passed to the
  same service. `cms.scenarios.loader` remains the legacy YAML parser only; it
  must not remain a privileged catalog-registration route. Preserve its slug
  validation, path containment, `yaml.safe_load`, and
  `TypeAdapter(AnyScenarioTemplate)` while legacy YAML is supported.
- Keep legacy YAML defaults and DB custom scenarios compatible until their
  pack adaptation is deliberately scoped. Do not make `Scenario.definition`
  polymorphic, infer pack type from YAML shape/path/name, or reuse a legacy
  editor YAML import as a package importer.
- Reuse `risk_register.services.audit_log` / `AuditEvent` for a successful
  registration summary and `shared.log_sanitize` for operational logs. Record
  sanitized catalog id, contract/profile, resolver kind, digest, result, and
  request correlation only; provenance is evidence, not a raw audit payload.

## Security And Runtime Gates

- **Authoring/authentication:** browser entrypoints use
  `threat_research_required`; service entrypoints use
  `validate_cms_authoring_user`; DRF uses `CMS_WRITE_PERMISSIONS` (including
  exact `cms:authoring:write` scope). No entitlement or identity check may be
  added after those authorization gates.
- **Registration shape:** validate the common registration request once at the
  service boundary, then pass ACES package-source fields through the shared
  validator. Revalidate persisted rows before registry launchability, as the
  registry already does. Keep ACES parser/conformance and backend-manifest /
  realizability gates authoritative rather than duplicating their schemas.
- **Reference/storage:** repository references retain containment checks in
  `shared.aces.package_loader.resolve_scenario_path`. Object-backed material,
  when #1567 supplies it, must use `shared.cloud.get_object_storage()` and its
  `ObjectStorage` protocol, normalized server-controlled keys, and immutable
  identity/digest checks. Do not accept arbitrary request URLs, filesystem
  roots, bucket names, or a client-controlled resolved path.
- **Secrets and processes:** no acquisition token, registry credential,
  presigned URL, private key, package body, rendered configuration, or provider
  diagnostic reaches logs, audit, API responses, docs, CI output, environment
  files, or process argv. Registration and request-time validation must not
  execute package scripts, shell commands, Docker, Terraform, or cloud CLIs.
- **Errors:** translate service failures through `cms.exceptions.CMSError` and
  `shared.errors`; DRF responses use `shared.api.errors`. Return stable,
  non-sensitive error classes, never parser traces, package fragments, storage
  payloads, or path/ownership internals.

## Extensibility And Current Constraint

The required seam is the registration input's explicit contract/profile and
resolver kind, plus its immutable package/lock identity. A future resolver or
ACES profile extends its resolver/contract adapter and the central allowlists;
it does not add branches to bootstrap, editor, CTF, Mission Control, engine, or
provisioner call sites.

Current code lists `object` as a valid source kind, but the native package
loader resolves only under `ACES_PACKAGE_ROOT`. Until #1567 provides an
object-storage resolver with the same containment and immutable-identity
properties, an object-backed row must not be registered as runnable or marked
launchable. This issue does not move #1567.

## Gotchas, Anti-Patterns, And Non-Goals

- Do not create separate "built-in", "marketplace", "licensed", "private",
  or "author-created" import endpoints, schemas, tables, permissions, audit
  events, exceptions, or bootstrap code paths.
- Do not conflate operator authorization to register content with entitlement
  to acquire it, or conflate a producer trust signal with an operator access
  decision.
- Do not let a new pack shadow an active legacy `scenario_id`; retain the
  registry's fail-closed collision behavior and ADR-024 cutover posture.
- Do not turn staff-review visibility, `conformance_status == "passed"`, or a
  valid digest into launchability; keep access, conformance/realizability, and
  launchability as separate axes.
- No marketplace/acquisition client, licensing or subscription service,
  hosted registry, credential store, package-source migration (#1567), image
  distribution redesign, ACES cutover, or replacement of legacy YAML/DB
  scenarios is in scope.

## Whole-Repository Surfaces

Implementation must evaluate `docs/architecture/uniform-content-ingestion-contract.md`,
`docs/architecture/product-development-surfaces.md`, ADR-024 and ADR-034 in
`docs/adr/index.yaml`, the ACES catalog preflights (#1232, #1252, #1253,
#1254), `cms/models/scenarios.py`, `cms/scenarios/{loader,registry}.py`,
`cms/services`, `cms/scenario_editor`, `cms/api`, `shared/schemas/aces_package_source.py`,
`shared/aces/{package_loader,manifest,realization_ledger}.py`,
`shared/{auth,api/errors,cloud,log_sanitize}.py`, `config/_aces_settings.py`,
the CTF/Mission Control service bridges, and `.importlinter`,
`scripts/check_layer_imports/layer_imports.yaml`, and
`scripts/adr_guard/adr_guard.py`.

The documentation gate is:

```bash
python3 scripts/adr_guard/adr_guard.py --all --level ci
```
