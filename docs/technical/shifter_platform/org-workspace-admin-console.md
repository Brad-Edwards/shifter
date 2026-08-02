# Organization/workspace admin console

The admin console (issue #1938, PLAT-231) is an SPA shell over the existing
workspace tenancy layer (ADR-046). It adds one read-only API and the client
shell, routing, workspace context, and switcher; it introduces no organization
authority model, no persisted workspace selection, and no new feature flag. The
binding boundaries are recorded in ADR-046-R11 and
[`organization-workspace-admin-console-preflight-1938.md`](../../architecture/organization-workspace-admin-console-preflight-1938.md).

## Current-principal context API

`GET /api/v1/workspaces/context/` returns the caller's existing workspace
memberships as a read-only projection: for each membership, the organization
(public UUID and name), the workspace (public UUID, name, and personal marker),
the caller's role, and the `WorkspaceOperation` codes that role permits.

- **Domain.** The view lives in the `workspaces` app
  (`workspaces/api/views.py:PrincipalWorkspaceContextView`) and reads through the
  service facade (`workspaces.services.list_actor_workspace_contexts`). No
  `config`, `management`, or frontend code imports workspace models or queries
  memberships directly.
- **Authorization.** Staff-session only. The bearer-first authentication chain
  parses an invalid token fail-closed before session fallback, and
  `IsStaffSession` rejects any valid platform token (including one owned by a
  staff user); the console read is deliberately not token-capable. Staff
  admission and per-workspace authority are additive and independent.
- **Capabilities.** Advertised capabilities are derived centrally from the
  role-to-operation policy (`workspaces.roles.role_permits`), so no consumer
  re-derives authority from a role string. They are advisory display data; every
  resource endpoint reauthorizes the operation it performs.
- **Side-effect free.** The GET reads existing rows with one bounded
  `select_related` query. It never creates or repairs tenancy state (it never
  calls `resolve_personal_workspace`), so a staff caller with no membership
  receives an empty page. The result is multi-organization-safe and paginated
  with the canonical page-number pagination.
- **Contract.** The serializer is authoritative; the generated OpenAPI
  (`openapi/v1.json`) and TypeScript types (`frontend/src/api/schema.d.ts`) are
  regenerated with `npm run gen:api`.

## SPA shell

The shell reuses the existing `/administer` area and the `administer_spa`
rollout; the Django host already serves `/administer/*` deep links when the flag
is on.

- **Routing.** An `organization` subtree under the `administer` route group
  (`frontend/src/router.tsx`), with path builders in
  `frontend/src/features/administer/routes.ts`. Organization-level slots sit
  under `/administer/organization`; workspace-scoped slots sit under
  `/administer/organization/workspaces/:workspaceUuid`.
- **Selection.** The selected workspace is the public-UUID route parameter,
  resolved against the context query by `resolveSelectedWorkspace`
  (`features/administer/organization/surfaces.ts`). A missing selection falls
  back to the first workspace; an invalid or stale UUID resolves to a
  "not found" state and never silently becomes another (or the personal)
  workspace. TanStack Query owns the server snapshot; the React context
  (`WorkspaceContext.tsx`) and the URL are not authority.
- **Navigation.** The "Organization" entry is registered in the central
  navigation contract (`frontend/src/app/nav.ts`), staff-gated and
  `administer_spa`-gated. The in-console section navigation is capability-aware
  (`surfaceEnabled`): sections whose required `WorkspaceOperation` the role does
  not permit are shown disabled. The Django admin escape hatch remains an
  unflagged external entry at `/admin/`, outside this subtree.
- **Slots.** The child surfaces are route slots rendering a placeholder
  (`ConsoleSlotPage`) until their owning issues (PLAT-234–240) land. The
  organization settings and workspace lifecycle slots are now real surfaces
  (see below).

## Organization profile & settings (issue #1939, PLAT-232)

The first console surface to replace a placeholder. It adds an organization
authority model and a UUID-keyed read/update API for the organization profile.
The binding boundaries are recorded in ADR-048, ADR-046-R12, and
[`organization-profile-settings-preflight-1939.md`](../../architecture/organization-profile-settings-preflight-1939.md).

### Organization authority (ADR-048)

ADR-046-R8 left the tenancy layer with no organization-wide role. ADR-048 is the
separately accepted authority model ADR-046-R12 requires:

- **Persisted role.** `OrganizationMembership` (`workspaces/models/_organization_membership.py`)
  is a `(organization, user, role)` row with a closed, database-checked
  `OrganizationRole` vocabulary (initially `admin`), owned by the `workspaces`
  domain and reachable only through `workspaces.services`.
- **Never derived.** Read/update authority is resolved only from an `admin`
  membership for that organization—never from a `WorkspaceRole`, Django
  `is_staff`/groups, Django model permissions, identity-provider claims,
  API-token scopes, or cached client capabilities.
- **Superuser override.** A Django `is_superuser` is the sole platform-operator
  override: it may read/update any organization, recorded distinctly in audit
  (`superuser_override`). `is_staff` without superuser or an admin membership is
  denied. The override lives in the service, not as an HTTP-layer permission
  shortcut.
- **Bootstrap.** Each personal organization (ADR-046-R4) seeds its personal
  workspace owner as its bootstrap admin—once, at `resolve_personal_workspace`
  creation and by the idempotent backfill
  (`workspaces/migrations/0005_backfill_organization_admins.py`). Authority is
  read from the row afterwards, never re-inferred from the workspace role.

### Profile API

`GET`/`PATCH /api/v1/workspaces/organizations/<uuid>/`
(`workspaces/api/views.py:OrganizationProfileView`) read and partially update
the profile (`name`, `description`, `support_email`, `support_url`).

- **Identifiers.** The organization is addressed by its immutable public `uuid`;
  the integer primary key never appears on the wire.
- **Discovery.** `GET /api/v1/workspaces/organizations/`
  (`OrganizationListView`) is the authority-owned list of organizations the
  caller may administer—a superuser sees all, everyone else sees only their
  `admin`-membership organizations. Discovery is never derived from workspace
  reachability (`list_administrable_organizations`).
- **Authorization.** Enforced in `workspaces.services.get_organization_profile` /
  `update_organization_profile`. A missing organization, an organization outside
  the actor's authority, and insufficient authority return one opaque `403`, so
  the endpoint is not a tenant-enumeration oracle.
- **Domain validation.** `update_organization_profile` validates unknown fields,
  lengths, a non-blank name, and email/URL formats in the service
  (`_validate_changes`), so the invariants hold for every facade caller, not only
  the HTTP serializer path (ADR-046-R12).
- **Session-only.** The bearer-first chain refuses an invalid token fail-closed;
  `IsAuthenticatedSession` rejects any valid platform token. Token access would
  require new exact scopes and is out of scope.
- **Serializers.** Explicit read and partial-update serializers (no writable
  `ModelSerializer`); unknown fields are rejected, and `EmailField`/`URLField`
  bound and format-check the input at the HTTP boundary.
- **Update semantics.** `update_organization_profile` is atomic: it
  `select_for_update`s the organization, re-checks authority under the lock,
  writes only fields whose value actually changed (absent is unchanged, empty
  string clears), and emits one strict `shared.audit` event
  (`AuditEntityType.ORGANIZATION`) carrying the changed field *names*, the
  internal organization id, and the `superuser_override` flag—never the field
  values—in the same transaction. A no-op change writes nothing and records no
  audit event.
- **Contract.** Regenerated into `openapi/v1.json` and
  `frontend/src/api/schema.d.ts` via `npm run gen:api`.

### SPA settings surface

`features/administer/organization/OrganizationSettingsPage.tsx` replaces the
settings slot with a chooser (`/administer/organization/settings`) and an editor
(`/administer/organization/settings/:organizationUuid`). The chooser lists the
organizations the caller may administer from the authority-owned list endpoint
(`useAdministrableOrganizations`), links each to its editor by public UUID, and
opens the only one directly; selection is never inferred from workspace context.
The editor uses the shared `useOrganizationProfile`/`useUpdateOrganizationProfile`
hooks (`frontend/src/api/organization.ts`), submits only fields changed from the
loaded snapshot (the PATCH mask, so a stale form cannot revert a concurrent
edit), surfaces server field errors from the shared `ApiError` envelope, and
never compares roles client-side.

## Workspace lifecycle (issue #1940, PLAT-233)

The second console surface to replace a placeholder. It adds the workspace
create/list/rename/archive/restore/owner-transfer lifecycle behind the existing
`workspaces.services` facade. The binding boundaries are recorded in ADR-046,
ADR-048, and
[`workspace-lifecycle-preflight-1940.md`](../../architecture/workspace-lifecycle-preflight-1940.md).

### Two distinct authorities

Lifecycle deliberately keeps organization authority and workspace authority
separate (ADR-046-R8, ADR-048), never conflating them:

- **Create and list** are organization-authorized. They resolve the target
  organization by public UUID through `resolve_administrable_organization`
  (`workspaces/services/_organization.py`), which reuses the ADR-048 `admin`
  `OrganizationMembership` seam (or the recorded superuser override). Authority
  is never derived from a workspace role, Django staff/groups, model
  permissions, identity claims, or API-token scopes.
- **Read detail, rename, archive, restore, and transfer** are authorized by the
  workspace role seam for that exact public workspace UUID, via new
  `WorkspaceOperation` codes (`read_workspace`, `rename_workspace`,
  `archive_workspace`, `restore_workspace`, `transfer_ownership`). The
  operation-to-role mapping lives only in `workspaces.roles.ROLE_OPERATIONS`:
  owner and admin may read/rename/archive/restore; `transfer_ownership` is
  owner-only. `create_workspace` seeds the creator as `OWNER` in the same
  transaction, so a create-then-manage flow works without a separate grant.

### Service (`workspaces/services/_lifecycle.py`)

- **Transactional and locked.** Each mutation locks the workspace row and
  re-checks the live grant under the lock (reusing
  `_memberships._lock_workspace_and_actor`), performs the change, and writes one
  strict, request-attributed `shared.audit` event (`AuditEntityType.WORKSPACE`)
  in the same transaction, so an audit-write failure rolls the mutation back.
  Audit records internal integer IDs, the action, and bounded state/field
  *names* only—never the workspace or organization display name.
- **Archive is a reversible marker.** A nullable `Workspace.archived_at`
  timestamp; archive sets it, restore clears it. It never deletes, rehomes, or
  cascades to the scalar `workspace_id` range bindings in CMS/Engine (those are
  `IntegerField`s, not foreign keys). List defaults to active-only with an
  explicit `include_archived` filter.
- **Invariants.** Names stay unique within the organization (DB constraint;
  `IntegrityError` is classified as `name_taken`, not surfaced raw). Owner
  transfer promotes the target's existing active membership to `OWNER` and
  demotes the acting owner to `ADMIN` in one atomic command, preserving the
  last-owner invariant throughout. Personal compatibility workspaces are
  rejected from every lifecycle mutation (`personal_workspace_protected`).
- **Opaque denials.** A malformed UUID, an unknown workspace/organization, and
  an unauthorized one all raise the same opaque denial, so the surface is not a
  tenant-enumeration oracle.

### Lifecycle API

Mounted under `/api/v1/workspaces/` (`workspaces/api/views.py`,
`workspaces/api/urls.py`):

- `GET/POST /api/v1/workspaces/` — list (`?organization=<uuid>&include_archived=&search=`) / create.
- `GET/PATCH /api/v1/workspaces/<uuid>/` — detail / rename.
- `POST /api/v1/workspaces/<uuid>/archive/`, `.../restore/`, `.../transfer/`.

- **Session-only, service-seam authorized.** Like the organization profile
  endpoints (ADR-048, PLAT-232), the lifecycle views use
  `IsAuthenticatedSession` with a bearer-first chain that refuses a valid
  platform token; domain authority is enforced inside `workspaces.services`.
  The console `/administer` route stays staff-gated at the SPA level for
  defense-in-depth, but a non-staff organization admin can drive the API for
  their own organization—`IsStaffSession` is deliberately *not* used here, so
  workspace authority is not collapsed into Django staff.
- **Serializers.** Explicit read (`WorkspaceSerializer`) and command
  (`CreateWorkspaceSerializer`, `RenameWorkspaceSerializer`,
  `TransferWorkspaceOwnershipSerializer`) serializers; the public workspace and
  organization UUIDs only on the wire. Bounded service outcomes map through the
  shared error envelope (`name_taken`→409, invalid name→400,
  `personal_workspace_protected`→409, `membership_not_found`→404, authorization
  →opaque 403).
- **Contract.** Regenerated into `openapi/v1.json` and
  `frontend/src/api/schema.d.ts` via `npm run gen:api`.

### SPA lifecycle surface

`features/administer/organization/WorkspaceListPage.tsx` replaces the workspaces
slot (organization-scoped list, search, include-archived toggle, and create),
and `WorkspaceDetailPage.tsx` renders the workspace-scope overview (rename,
archive/restore with a confirm dialog, and owner transfer). Both use the shared
`frontend/src/api/workspaces.ts` TanStack Query hooks (one typed client and
query-key family; mutations never auto-retry and invalidate the affected
caches), address workspaces by public UUID only, and never compare roles
client-side.
