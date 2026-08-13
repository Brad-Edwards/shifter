---
id: PLAT-235
title: "Member invitations and onboarding"
status: ACTIVE
type: FUNCTIONAL
priority: MUST
created_at: 2026-08-01T17:37:05.299523Z
updated_at: 2026-08-01T17:41:19.898031Z
---

# PLAT-235: Member invitations and onboarding

## Statement

The platform shall provide an email invitation flow for adding members who may not yet have an account, using signed, expiring tokens issued and verified server-side, with resend and revoke, plus an SPA surface to issue and track invitations (pending/accepted/expired/revoked) with role assignment. The flow shall reuse the platform's existing email and token infrastructure rather than a bespoke token scheme.

## Rationale

The current add-member path requires an already-existing user, which does not support onboarding new people into a shared deployment. A signed-token email invitation is the standard, proven pattern and should reuse existing email/token infrastructure rather than be hand-rolled.

## Traceability

- IMPLEMENTS → GITHUB_ISSUE `1942`
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/workspaces/models/_invitation.py` (Persistent invitation lifecycle, role, generation, expiry, and terminal-state constraints)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/workspaces/services/_invitations.py` (Signed-token issue, resend, revoke, staging, acceptance, delivery, and exactly-once membership service)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/workspaces/api/invitation_views.py` (Session-authorized invitation administration API)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/config/workspace_invitation_auth.py` (Fresh verified-identity login handoff for invitation acceptance)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/workspaces/public_views.py` (Credential-safe public invitation staging and acceptance flow)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/frontend/src/api/invitations.ts` (Generated-contract TanStack Query invitation data layer)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/frontend/src/features/administer/organization/WorkspaceInvitationsPage.tsx` (Invitation issue, status, resend, and revoke administration surface)
- IMPLEMENTS → DOCUMENTATION `docs/technical/shifter_platform/workspace-member-invitations.md` (Invitation lifecycle, trust boundaries, deployment behavior, and operator controls)
- TESTS → TEST `shifter/shifter_platform/tests/workspaces/test_invitations.py` (Signed token, expiry, rotation, revoke, acceptance, authorization, audit, and membership invariants)
- TESTS → TEST `shifter/shifter_platform/tests/workspaces/test_invitation_api.py` (Invitation API authentication, authorization, request, projection, and mutation behavior)
- TESTS → TEST `shifter/shifter_platform/tests/workspaces/test_invitation_public_flow.py` (Fragment staging, fresh-login continuation, verified-identity handoff, and public acceptance behavior)
- TESTS → TEST `shifter/shifter_platform/tests/workspaces/test_invitation_concurrency_postgres.py` (PostgreSQL race coverage for exactly-once invitation and membership outcomes)
- TESTS → TEST `shifter/shifter_platform/frontend/src/features/administer/organization/WorkspaceInvitationsPage.test.tsx` (Invitation administration UI behavior and accessibility coverage)
- TESTS → TEST `shifter/shifter_platform/frontend/src/test/workspace-invitation-accept.test.ts` (Browser-side fragment exchange, history scrubbing, and bounded error coverage)
