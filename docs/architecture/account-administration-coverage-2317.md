# Account administration coverage and S8 handoff (#2317)

ADR-066 keeps legacy production authority in place through S4. The policy-aware
adapters below are deliberately unmounted; routing or replacing a legacy gate
before S8 would create two competing authorities. This inventory records exact
action targets and the remaining work for the coordinated cutover. It is not a
claim that the issue's acceptance criteria are complete.

| Operation family | Target and action | S4 enforcement and tests | S8 handoff |
| --- | --- | --- | --- |
| Account create | installation / `installation.manage_accounts` | `admin_create_account`; human/service, ceiling, denial, strict structural audit | Mount collection route and account creation UI. |
| Account list/detail/current individual | account / `account.read`; installation administrator list override | `admin_list_accounts`, `admin_get_account`, `admin_individual_account`; filtering before count/page and exact target tests | Mount routes and individual account view without an organization selector. |
| Organization list/detail/create | organization / `organization.read`; account / `account.manage_organizations` | `admin_list_organizations`, `admin_get_organization`, `admin_create_organization`; sibling/parent tests | Mount routes and business account screens. |
| Workspace list/detail/create | workspace / `workspace.read`; organization / `organization.manage_workspaces` | `admin_list_workspaces`, `admin_get_workspace`, `admin_create_workspace`; sibling/parent/archived tests | Mount routes and business organization screens. |
| Account membership list/add/remove | account / `account.manage_members` | `admin_list_account_members`, `admin_add_account_member`, `admin_remove_account_member`; explicit membership and strict audit tests | Mount member routes and account member UI. |
| Workspace quota and egress authoring | workspace / `workspace.manage_quota`, `workspace.manage_egress` | Policy commands reuse locked writers; service principal, denial, archived, audit and direct API tests | Mount policy routes; retire superuser and workspace-role authority for these commands. |
| User list/detail/lifecycle/set-active/delete/reset | installation / `installation.manage_principals` | Prepared `PolicyAdminUser*View` classes and guarded lifecycle/reset services; service principal, ceiling, invariant and API tests | Replace staff/model-permission routes and retain the v1 response contract. |
| Global audit list/detail | installation / `installation.read_audit` | `authorized_audit_events` and `PolicyAuditLogViewSet`; service, ceiling, provider-loss and list/detail tests | Replace the staff-session route and document bearer-capable OpenAPI security. Historical rows remain installation-only. |
| Personal and service credential administration | installation / `installation.use_personal_tokens`, `installation.revoke_personal_tokens`, `installation.manage_service_credentials` | #2316 policy services and credential API remain the incumbent; this slice has not changed them | Verify full administrator, service, ceiling and browser ceremony cases in the combined route suite. |
| Workspace rename/archive/restore | workspace / `workspace.update`, `workspace.archive`, `workspace.restore` | Prepared policy commands reuse locked legacy writers; service principal, denial, archive and API tests | Mount policy routes and retire human-only role authority for these commands. |
| Workspace ownership and membership | workspace / `workspace.transfer`, `workspace.manage_members` | Prepared single-workspace ownership transfer checks policy for a service actor, source owner and existing destination membership, then reuses the locked/audited writer. Legacy role-authorized commands and routes remain the sole production path. | Expose the policy transfer through public principal UUIDs and prepare membership commands for service administrators; replace human-only routes at cutover. |
| Workspace invitations | workspace / `workspace.manage_invitations` | Legacy human-role service and routes remain the sole production path | Add a principal-aware invitation command, delivery and acceptance mapping without deriving a grant from email or creator. |
| Scoped group/policy assignment | account, organization, workspace / matching `manage_authorization` and delegated action checks | #2315 supplies the durable policy journal; prepared account/organization metadata, group membership, policy, predefined, direct assignment and operation adapters use exact SQL ancestry and the existing journal. Service and API tests cover parent mismatch, exact target and credential ceiling. | Mount account and organization routes; run released-provider delegation and cross-scope suites with the full role catalog. |
| Cross-domain organizer grant and offboarding transfer | installation / `installation.manage_principals`; source and destination resource actions | Current `config.api_administer` staff/superuser route remains unchanged | Check each persisted source and requested destination through owning services; preserve CMS/Engine/workspace transfer invariants and strict attribution. |
| Django admin and legacy browser writes | Same action as the owning operation | Current quota admin delegates to the legacy service; profile admin has direct writes | Route retained writes through policy services or disable them after equivalent canonical UI/API operations are live. |
| Counts, search, export and download | Action of the underlying collection/resource | Prepared account/subdivision/member lists filter before count and page. No account or audit export/download endpoint exists in the current v1 modules. | Apply the same authorized projection before every retained search, aggregation, export and download in the routed S8 surface. |

The S8 route swap must be atomic with #2321's mapping and authority cutover.
Before activation, compare this inventory with the full v1 URL set and
`ACTION_CATALOG`, run PostgreSQL and released OpenFGA behavior suites, regenerate
OpenAPI and frontend types, and test browser navigation and account selection.
