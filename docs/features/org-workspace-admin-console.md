# Organization/workspace admin console

The organization/workspace admin console is the staff-facing area for
administering organizations and workspaces. It lives under **Administer →
Organization** in the platform navigation and is the shell that the
per-capability administration surfaces (membership, invitations, users, range
scoping, policy, quota, and audit) attach to as they ship.

## Access

- The console is visible only to staff accounts and only when the
  `administer_spa` rollout flag is on. When the flag is off, the console and its
  navigation entry are hidden and the routes are not served.
- Access to the console (a staff session) is separate from authority inside a
  workspace. Being staff lets you open the console; it does not grant any
  workspace operation. Each workspace action is authorized by your role in that
  workspace, and the server re-checks it on every request.
- **Django admin** remains available as a full-page escape hatch from the
  Administer sidebar in every rollout state, for the deep or rarely used
  administration the console does not duplicate.

## Working in the console

- The console lists the organizations and workspaces you belong to. Your role in
  each workspace is shown next to it.
- A **workspace switcher** selects the workspace you are administering. The
  selected workspace is part of the page address, so you can bookmark or share a
  link to a specific workspace. Opening a link to a workspace you do not belong
  to shows a "workspace not found" message rather than silently switching you to
  a different workspace.
- Within a selected workspace, the section navigation reflects what your role
  permits: sections your role cannot use are shown but disabled. This is a
  display aid only; the underlying endpoints enforce access regardless of what
  the navigation shows.

## Organization settings

**Administer → Organization → Organization settings** lets an organization
administrator view and edit their organization's profile: its display name,
description, support email, and support URL. If you administer more than one
organization the surface first lists them so you can choose which to edit;
otherwise it opens the one you administer directly.

- **Who can edit.** Editing an organization is restricted to that
  organization's administrators. Being able to open the admin console (a staff
  session) does not by itself let you edit an organization—organization
  administration is a separate authority. A platform superuser can administer
  any organization; every such change is recorded in the audit log.
- **What is saved.** Only the fields you change are written. Leaving a field
  empty clears it. Invalid input (for example a malformed support email) is
  rejected with a message next to the field, and nothing is saved until it is
  corrected.
- Organization logos and other branding assets are not part of this surface;
  they will arrive with their own release once their storage and safety handling
  is defined.

## Workspaces

**Administer → Organization → Workspaces** lists the workspaces in an
organization you administer and lets you create and manage them. If you
administer more than one organization you choose the organization first;
otherwise it opens the one you administer.

- **Create.** Give the workspace a name that is unique within the organization.
  You become its owner. Personal workspaces are created automatically for each
  user and are not created or managed here.
- **List and search.** The list shows active workspaces by default; a toggle
  includes archived ones. Search filters by name.
- **Rename.** A workspace owner or admin can rename a workspace. Names stay
  unique within the organization.
- **Archive and restore.** Archiving hides a workspace from the default list
  without deleting anything—ranges bound to the workspace are left untouched.
  Restoring brings it back. Archiving is fully reversible.
- **Transfer ownership.** A workspace owner can hand ownership to another
  existing member of the workspace: the new owner is promoted and the previous
  owner becomes an admin, so the workspace always keeps an owner.
- **Who can do what.** Creating and listing workspaces requires organization
  administrator authority for that organization. Renaming, archiving, restoring,
  and transferring are authorized by your role in that specific workspace (owner
  or admin; transfer is owner-only). The server re-checks every action; the
  console never decides access on its own. Personal workspaces cannot be
  created, renamed, archived, or transferred here.

## What is not here yet

This release delivers the console shell, navigation, workspace context,
switcher, the organization settings surface, and the workspace lifecycle
surface above. The remaining administration surfaces (membership, invitations,
user lifecycle, range scoping, policy, quota, and audit review) arrive in later
releases; their sections are present as placeholders until then.
