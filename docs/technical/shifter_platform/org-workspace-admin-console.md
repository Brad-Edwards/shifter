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
  (`ConsoleSlotPage`) until their owning issues (PLAT-232–240) land.
