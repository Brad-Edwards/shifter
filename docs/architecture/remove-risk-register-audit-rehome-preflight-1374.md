# Remove Risk Register and Rehome Audit Preflight (#1374)

Status: pre-implementation architecture guidance

Date: 2026-07-24

Issue: GitHub #1374, "Remove the Risk Register feature; rehome the platform
audit subsystem to shared"

Requirement: none. The GitHub issue title, body, constraints, and acceptance
criteria are the shipping contract. This note records repo-wide design
guardrails; it is not an implementation plan and does not implement the issue.

## Scope Boundary

This is a rehome-then-remove change, not a flat feature deletion.

Keep these concepts separate:

1. The Risk Register product feature: risks, comments, severity/status/STRIDE
   schemas, UI/routes/API/MCP tools, rollout flags, Cognito-group gate, docs,
   tests, and generated client types. This is removed.
2. The platform audit subsystem: durable `AuditLog` rows, the `shared.audit`
   writer contract, request attribution, failure policy, health signal, read
   API, admin visibility, and archive command. This survives under `shared`.
3. The retired `APIKey` model: archival credential metadata only. It is not a
   runtime authentication mechanism and must not be revived while moving it.
4. Historical audit vocabulary versus active emitter vocabulary. Future code
   must stop emitting risk/comment events, while preserved historical rows must
   still be readable truthfully.

## Architecture Decisions And Guardrails

- `shared.audit` remains the single neutral audit contract. It already owns the
  action/entity/actor vocabulary, `AuditEvent` and related value objects,
  request attribution helpers, strict/best-effort policy, sanitized logging,
  health state, and the `AuditWriter` binding. Do not fork those into a second
  package or per-domain audit helper.
- The concrete Django writer moves from the `risk_register` compatibility
  adapter to `shared`, but it still satisfies the same `AuditWriter` port.
  Direct `AuditLog.log()` calls remain confined to the concrete writer; emitters
  keep using `shared.audit.audit_log*`.
- Binding stays at the composition/startup boundary in
  `config.apps.PortalConfig.ready()`. `shared` must not import `config`, feature
  domains, presentation modules, or a service locator to discover its writer.
- The durable store belongs to the installed `shared` Django app. Do not create
  an `audit` Django app, an event bus, an outbox, a separate archive table, a
  duplicate API schema, or a second audit exception hierarchy for this removal.
- `/api/v1/audit/` remains read-only and admin-only. The old risk-register group
  name must not survive as an audit policy. If the implementation needs an extra
  audit-read gate beyond staff/superuser, introduce an explicitly audit-named
  policy and setting through the existing config/env validators; do not keep
  `RISK_REGISTER_ALLOWED_COGNITO_GROUPS` as a disguised audit knob.
- Programmatic access to audit reads stays absent unless a dedicated audit-read
  scope is explicitly accepted. Do not reuse `risk:read` or `risk:write`, and
  do not place `shf_` tokens in the browser.
- Audit persistence migrations must handle both upgraded databases and fresh
  installs after `risk_register` leaves `INSTALLED_APPS`. A migration that only
  depends on historical `risk_register` migrations is not enough once the app is
  removed from settings and code.
- Risk-specific tables may be removed only after the audit/APIKey state is
  owned by `shared` and the management migration dependency has been repointed.
  `AuditLog.entity_id` has no FK to risk rows, so audit preservation is a
  separate invariant from retaining pilot risk data.
- Generated artifacts must be regenerated from the live server contracts:
  `openapi/v1.json`, `frontend/src/api/schema.d.ts`, and
  `locale/en/LC_MESSAGES/django.po`. Do not hand-edit generated schemas or
  TypeScript DTOs.
- Accepted ADRs and docs that still state "Risk Register is canonical" must be
  updated, superseded, or deleted in the same implementation change. Do not
  leave an accepted ADR claiming a removed feature is authoritative.

## Canonical Incumbents To Reuse

| Concern | Incumbent | Required reuse |
| --- | --- | --- |
| Audit contract and policy | `shared.audit` (`events.py`, `policy.py`, `port.py`, `vocabulary.py`, `attribution.py`, `health.py`) | Keep one event shape, one vocabulary owner, one writer binding, one strict/best-effort policy, and one trusted client-IP resolver. |
| Startup wiring | `config.apps.PortalConfig.ready()` | Bind the shared concrete writer once at startup; missing/conflicting binding remains a startup configuration error. |
| Audit health | `config.health_checks.AuditLogDegradedHealthCheck`, `config.health.CoarseHealthCheckView` | Preserve degraded audit visibility through the existing coarse health surface; do not expose payloads or raw exceptions. |
| API error envelope | `shared.api.errors.api_exception_handler` / `api_error_response` | Audit API 401/403/validation errors keep the platform envelope; no raw ORM or storage exceptions reach clients. |
| API auth and permissions | DRF defaults in `config._drf_settings`, `shared.api.permissions.IsStaffSession`, `shared.api_tokens.authentication`, `shared.api_tokens.permissions.require_scope` | Keep browser reads session based, reject tokens unless a dedicated scope is accepted, and publish true scope metadata through `PlatformAutoSchema`. |
| OpenAPI contract | `shared.api.schema.PlatformAutoSchema`, `config.management.commands.api_contract`, `frontend` `npm run gen:api` | Let the live DRF surface drive OpenAPI and generated TS types; no duplicate hand schemas. |
| SPA client and nav | `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/app/nav.ts`, `frontend/src/router.tsx`, `RootLayout` route handles | Remove the risk surface from the one router/nav/bootstrap contract; do not create a parallel client or hidden route registry. |
| Layer policy | `scripts/check_layer_imports/layer_imports.yaml`, `.importlinter`, `scripts/adr_guard/adr_guard.py`, `check_model_fks.py` | Update the canonical app classification and every parity-checked copy together; no stale `risk_register` package in guardrails. |
| Migration pattern | Existing `SeparateDatabaseAndState` migrations in `management`, `engine`, and `cms` | Use repo-native state/database split patterns for model rehomes and table adoption/rename; support fresh and upgraded databases. |
| MCP ops policy | `.shifter.yaml`, `mcp/ops/policy.js`, `mcp/ops/audit.js`, `registerTool`, `mcp/ops/respond.js` | Delete risk-specific tools, keep generic policy/audit gates, and classify the rehomed audit table as shared in DB inspection. |
| Secret and log hygiene | `shared.log_sanitize`, `config._logging_config`, MCP audit redaction | Keep tokens, cookies, provider payloads, raw audit state, and full exceptions out of logs, env, argv, health, and MCP responses. |

## Cross-Cutting Layers To Pass

- Auth surface: `/api/v1/audit/` remains authenticated and read-only. Browser
  access uses session cookies from the same Django origin. API-token auth must
  be rejected for audit reads unless a new audit-specific scope is deliberately
  accepted and registered in `shared.api_tokens.scopes`.
- Authorization surface: UI visibility, bootstrap flags, and nav entries are
  advisory only. The audit endpoint itself must enforce staff/superuser or an
  explicit audit-read policy server-side. Removed risk routes must resolve to
  404, not an access-denied page that implies a hidden product still exists.
- Request attribution: audit writers continue using
  `shared.audit.get_client_ip()` and `get_request_id()` so the configured
  rightmost trusted proxy hop and middleware request id remain canonical. No
  view-local XFF parser should reappear.
- Secret handling: `AuditEvent.previous_state`, `new_state`, context, request
  metadata, archive payloads, logs, health output, generated docs, and MCP
  audit records must not contain raw bearer tokens, cookies, CSRF tokens,
  passwords, private keys, provider credentials, full headers, or unbounded
  exception strings.
- Env-binding shape: remove risk-only settings and generated env docs. Any new
  audit setting must use the existing split-settings `_env_*` pattern, appear in
  `__all__`, and be reflected in `config/env-manifest.json` only when it becomes
  a runtime contract. No secret should be introduced for this removal.
- Config validators and gates: `.importlinter`, custom layer imports,
  `adr_guard`, installed-app classification, model-FK checks, API contract drift,
  frontend typecheck/test, and MCP surface/policy tests must all see one
  coherent post-risk world.
- OS/runtime exposure: the audit write path stays in-process through Django ORM
  and PostgreSQL. `audit_archive` may use boto3 as it does today, but it must not
  shell out, pass audit payloads in process argv, dump env, or write fallback
  local audit copies.
- Error envelopes: audit API failures use the shared DRF error envelope. Audit
  persistence failures remain internal policy decisions: strict paths re-raise
  into existing sanitized handling; best-effort paths mark audit health degraded.
- MCP operator surface: deleting risk tools must remove named risk DB writes and
  schemas without weakening `named_db_write`, `db_arbitrary`, two-phase, prod
  confirmation, idempotency, untrusted-input fencing, or audit-record redaction.

## Migration And Data Guardrails

- The shared model state and physical table state must converge without
  drop/recreate of `AuditLog` or `APIKey`. Use conditional, idempotent database
  operations where needed so an upgraded DB with old `risk_register_*` tables
  and a fresh DB with no old tables both end with working `shared` tables.
- Do not leave `management.0008` depending on `risk_register`. Its historical
  `apps.get_model()` lookup must target the new shared audit model so a fresh
  install can apply migrations after the risk app is gone.
- Table-name migration must be reflected anywhere raw SQL names are part of an
  operator or test contract, notably `mcp/ops/lib.js` service-layer mapping and
  tests.
- Retired risk/comment audit rows are a read-compatibility issue, not an active
  emitter contract. If historical rows remain visible through `/api/v1/audit/`,
  the read serializer/OpenAPI contract must not lie about possible `entity_type`
  values. Keep any retired-value handling separate from active
  `AuditEntityType` constants so new code cannot emit risk events.
- Keep `AuditActorType.APIKEY` / `AuditEntityType.APIKEY` only as the platform
  API-token and historical retired-key audit vocabulary requires. Do not revive
  `rr_live_` credentials, mint paths, or old API-key authentication.

## Whole-Repo Scope In This Change

Likely in scope for the implementation:

- `shifter/shifter_platform/shared/` audit models, adapter, serializers, views,
  URLs, admin, migrations, management command, docs, and tests.
- `shifter/shifter_platform/config/` startup binding, root/API URLs, bootstrap,
  dashboard, context processors, settings, DRF schema settings, OIDC/SPA flags,
  env manifest, health registration tests, and API URL tests.
- `shifter/shifter_platform/risk_register/`, `templates/risk_register/`, and
  `tests/risk_register/` for deletion after rehome.
- Cross-app tests under `tests/config`, `tests/management`, `tests/cms`,
  `tests/ctf`, `tests/engine`, `tests/mission_control`, and `tests/shared`
  that assert persisted audit rows.
- Frontend routes, nav, bootstrap/dashboard types, fixtures, risk feature
  folder, generated schema, and e2e/unit tests.
- MCP ops tool registration, schemas, table classification, and surface/policy
  assertions.
- Architecture guardrails: `.importlinter`,
  `.github/quality-path-filters.yaml`, `scripts/check_layer_imports/*`,
  `scripts/adr_guard/*`, `check_model_fks.py`, and tests asserting parity.
- Docs and indexes that name Risk Register as a live feature or audit owner:
  feature docs, technical docs, risk audit architecture docs, ADR index,
  documentation coverage, and ADR README.

## Gotchas And Anti-Patterns

- Do not delete `risk_register` before binding a working shared audit writer;
  mission_control, ctf, cms, management, engine, and shared API-token flows
  already write audit rows.
- Do not make `shared` depend on `config` or feature domains while moving the
  adapter. The dependency direction remains emitters -> shared audit -> bound
  writer.
- Do not keep `RISK_REGISTER_SPA_ENABLED`,
  `RISK_REGISTER_ALLOWED_COGNITO_GROUPS`, `can_access_risk_register`,
  `risk_register_spa`, `/risk-register/`, `/api/v1/risks/`, or `risk:*` scopes
  as dormant compatibility affordances unless an explicit post-removal contract
  requires them. The issue calls for removal, not hiding.
- Do not leave the home/dashboard API carrying an empty `risk_register` object
  just to avoid frontend edits. That keeps a dead domain in the public contract.
- Do not clone risk serializers, validators, permissions, or DTOs into shared.
  Risk severity/status/STRIDE validation dies with the feature.
- Do not broaden audit read access accidentally by replacing the removed
  Cognito-group gate with `IsAuthenticated`.
- Do not hand-edit generated OpenAPI/TypeScript files or Django translations to
  make tests pass. Regenerate from the post-removal server and templates.
- Do not weaken or delete guardrail tests because they mention `risk_register`.
  Update the canonical classification and parity expectations to reject stale
  entries instead.
- Do not edit `CHANGELOG.md`; it is release-please owned.
- Do not treat `rg risk_register` as an absolute migration rule if a
  transitional shared migration must mention old table names for upgrade
  compatibility. Runtime imports, configs, live docs, routes, and contracts are
  the dangling-reference target.

## Non-Goals And Boundaries

- No issue implementation in this preflight.
- No new Ground Control requirement or traceability work.
- No new audit backend, queue, SIEM exporter, telemetry platform, or plugin
  architecture.
- No redesign of request attribution, API-token authentication, session/CSRF
  posture, health response shape, or MCP policy classes.
- No preservation guarantee for pilot Risk Register product rows beyond what
  the issue explicitly requires. The hard data invariant here is no audit data
  loss and no loss of archival token metadata needed to interpret audit history.
- No replacement risk-management feature, hidden admin-only risk UI, or
  sanitized GitHub-risk workflow in this issue.
