# Account hierarchy and platform administration preflight (#2317)

Status: S4 architecture guidance; source work remains subject to #2308's
authorization gate and production authority changes only at S8

Contract: issue #2317, under ADR-066 and the #2314–#2316 boundaries. This note
records integration guardrails, not an implementation plan.

## Decisions that close design risk

- `workspaces` owns Account, Organization, Workspace, explicit memberships,
  defaults, lifecycle, quota/egress, scoped policy metadata, and SQL ancestry.
  `management` owns Principal, provider binding, user lifecycle, and credential
  admission. `shared` owns the neutral identity/scope/action/decision and audit
  contracts. Cross-domain presentation composes public service facades in
  `config`; no feature app imports another domain's models or private services.
- An individual account is a complete customer scope. No account, organization,
  or workspace selector may be required for its ordinary flow. Team/enterprise
  accounts may have real subdivisions and default structure; creation never
  grants membership, ownership, role, policy, or cloud access to the creator.
  Membership, resource ownership, policy assignment, and provider identity are
  separate facts. An account type is not a role.
- Each protected operation maps to the exact registered
  `shared.authorization.ACTION_CATALOG` action checks and concrete
  `TargetRef`/server-resolved `ResourceScope`. The owning
  service receives a live `PrincipalRef`, `CredentialCeiling`, and configured
  provider, and enforces policy even when invoked without HTTP. Existing
  workspace role methods and superuser overrides are pre-S8 authority only;
  they cannot remain as an alternative S8 grant or an accidental second veto
  on full human/service application administrators. Domain lifecycle, quota,
  CTF, cloud, and owner invariants continue to compose separately.
- Installation-wide user, principal, authorization, credential, and audit
  operations target `installation`; customer operations target the exact
  account, organization, or workspace. Full application administrators are
  covered by the catalog's administrative set, subject to the credential's
  exact ceiling and ceremony. Delegated administrators must pass the existing
  bounded descendant and no-escalation checks for every effect; a broad
  parent role does not license a higher or sibling target. No authoring path
  writes OpenFGA tuples outside the durable `workspaces.services` mutation
  journal and recovery boundary.
- Collections derive an authorized queryset/projection *before* pagination,
  count, search, aggregation, export, or download. Detail and parent-scoped
  creation re-resolve the exact target and ancestry; changing a path, body,
  or selected UI scope cannot move a child to a sibling account. A missing,
  cross-account, denied, or operation-inadmissible archived target has an opaque external outcome.
  Every read or write variant needs a behavior test, including service calls.
- Ownership and scope transfer require authority over the persisted source
  and requested destination, destination eligibility, and consistency across
  affected owner/scope projections. Use the incumbent workspaces/CMS/Engine
  commands and locks for the resource kind; preserve stable IDs, historical
  attribution, membership and owner invariants. No synthetic membership,
  account-type inference, cloud grant, or partial projection repair.
- The current `workspaces.services.list_actor_workspace_contexts`,
  `config.api_bootstrap`, `frontend/src/api/principalContext.ts`, and
  Administer router/nav assume workspace membership or staff flags for display.
  Their S8 presentation projection must represent an individual account and
  service administrator without treating either hint as authority; resource
  endpoints still check policy.
- The shared audit ledger remains deployment-global and has no trustworthy
  account/organization/workspace field per row. Its current list and detail
  views therefore use `installation.read_audit`; a selected workspace or
  actor/entity lookup cannot make historical rows tenant-safe. A later scoped
  audit product must attach authoritative scope at emission and include it in
  the versioned integrity profile before adding a scoped action/filter. S4
  must keep existing rows readable only to installation audit authority.
- `/admin/` and legacy browser handlers are real mutation paths. Retained
  writes must call the same authorized service command with trusted actor and
  audit context; otherwise disable their write methods and expose the supported
  operation through the canonical API/UI. Django admin's human-session gate
  cannot be the only way a valid service administrator performs an operation.

## Canonical incumbents and layers to pass

| Layer | Reuse and required check |
| --- | --- |
| Identity/authentication | `config.identity_platform`, `config.oidc`, `shared.verified_identity`, `config.session_credentials`, `management.services`, `shared.api.principals.authenticated_credential`, and the bearer-first `config._drf_settings` chain. Exact issuer/subject, active principal, provider/MFA proof, session CSRF, CTF confinement, and service admission precede policy; a service never impersonates creator/contact. |
| Policy and ancestry | `shared.identity_scope`, `shared.authorization` catalog/contracts/port, `config.openfga_authorization`, `workspaces.services.resolve_resource_scope`, `_authorization_delegation`, `_authorization_policy`, and the operation journal. Validate target/action/ceiling, SQL ancestry and lifecycle, and live provider result. No provider tuple or UI cache becomes ancestry or authority; provider failures and incomplete batches deny. |
| Transport and schema | `management.api.views`, `management.admin_services`, `workspaces.api.lifecycle_views`, `workspaces.api.authorization_views`, `config.api_administer`, `shared.api.audit`, their named DRF commands, `ClosedJSONParser`/`ClosedSerializer`, query serializers, `shared.api.errors`, `shared.api.schema`, `openapi/v1.json`, and generated `frontend/src/api/schema.d.ts`/`types.ts`. HTTP validates shape; services validate state and authority. Preserve the sanitized request-ID envelope and v1 compatibility; never reflect SDK, SQL, or validation exception text. |
| Persistence and audit | `workspaces.models`, `management.models`, `transaction.atomic`/row locks/named constraints, `shared.audit` vocabulary/attribution/strict writes, `shared.audit_adapter`, and versioned integrity verification. Audit security mutations and denied policy effects with bounded IDs/codes; immutable historical evidence and policy journals are not workflow caches. PostgreSQL must prove concurrency and constraint behavior. |
| Secret and runtime binding | `config/env-manifest.json`, `config._env_manifest`, `config.openfga_authorization.OpenFgaRuntimeSettings`, installation `schema`/`loader`/`render`/`runtime_inventory`, and `platform/k8s`/Terraform bindings. This slice needs no new environment key or secret. Existing OpenFGA token is a bounded mounted file, URL is private TLS, and provider verification uses its configured audience/issuer. Any future binding goes through every canonical validator and deployment inventory, not an ad hoc `os.getenv`. |
| Browser and OS exposure | `frontend/src/api/client.ts` (`apiFetch`/bounded `apiDownload`), TanStack Query, `frontend/src/router.tsx`, Administer routes/nav, and existing CSP/cookie policy. Browser carries only same-origin session and CSRF; capabilities are hints. Public UUIDs may identify a route, never internal IDs or authority. Keep tokens, provider claims, policy tuples, audit contents, and customer data out of process argv, shell logs, cloud labels, guest metadata, public Vite config, browser storage, and secret-bearing URLs. Exports authorize and bound the server-side result before bytes leave. |
| Observability and workflow | `shared.log_sanitize`, request IDs, `shared.audit`, `.importlinter`, `scripts/check_layer_imports`, `check_model_fks`, `adr_guard`, OpenAPI drift/breaking checks, platform PostgreSQL and released OpenFGA harness, plus frontend type/a11y/route tests. Log bounded action, scope ID, outcome and reason code; no roster, email, credential, raw provider error or policy body. |

## Coverage and extension seam

The implementation's action/scope/enforcement inventory must cover every
baseline administer, audit, and workspaces operation across API, browser,
service, Django admin, legacy route, collection/count/export, and download.
Include an authorized human, authorized service credential, insufficient
credential ceiling, delegated ancestor, sibling account, revoked authority,
missing/archived target, and provider failure where applicable. Compare the
inventory with `ACTION_CATALOG`; adding a valid operation extends that one
catalog and its model/credential mapping, then maps its owning service and
published endpoint. The next account-scoped admin surface should vary by
`TargetRef` and resolved `ResourceScope`, not by a new role evaluator, separate
individual-account workflow, or duplicated API schema.

## Gotchas, anti-patterns, and non-goals

- Do not equate `is_staff`, `is_superuser`, `auth.change_user`, organization or
  workspace membership, CTF organizer, provider group, token scope, cloud IAM,
  or cached frontend capability with an application policy grant. Do not let
  an HTTP permission class replace the service check or block an otherwise
  valid service administrator solely because it expects a Django human.
- Do not copy an account tree into management, audit, OpenFGA, frontend state,
  or a cross-layer foreign key; do not infer installation scope from null IDs
  or force a fake default workspace onto an individual.
- Do not create a second action list, role matrix, schema/DTO, validator,
  exception envelope, audit log, transfer workflow, export filter, credential
  verifier, or OpenFGA client. Do not add a broad `all` action or wildcard
  credential to repair a missing mapping.
- Do not infer tenant audit visibility from current entity relations, audit
  JSON/context, actor membership, route parameters, or historical rows. Do
  not hash a new security-significant audit field under an old integrity
  profile or rewrite old evidence.
- Do not bypass services with Django-admin `save_model`/bulk actions, signals,
  management commands, or legacy views. Do not move network policy calls under
  SQL locks, silently retry uncertain tuple writes, or treat evaluator failure
  as denial evidence that a mutation did not happen.
- No new policy engine, auth protocol, token format, credential broker, cloud
  IAM grant, customer-specific deployment, new app, audit store, tenant-audit
  reconstruction, infrastructure/secret binding, or mixed-authority rollout.
  This preflight changes no runtime behavior and does not authorize source work
  beyond #2308's gate.
