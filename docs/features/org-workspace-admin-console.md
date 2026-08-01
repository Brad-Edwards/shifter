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

## What is not here yet

This release delivers the console shell, navigation, workspace context, and
switcher. The individual administration surfaces (organization settings,
workspace lifecycle, membership, invitations, user lifecycle, range scoping,
policy, quota, and audit review) arrive in later releases; their sections are
present as placeholders until then.
