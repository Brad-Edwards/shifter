---
id: PLAT-240
title: "Administrative audit log and activity history"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
created_at: 2026-08-01T17:37:40.529658Z
updated_at: 2026-08-01T17:41:19.898054Z
---

# PLAT-240: Administrative audit log and activity history

## Statement

The platform shall expose an administrator-facing, read-only audit/activity API over the existing shared.audit record and an SPA surface to search and filter significant administrative events (authentication, membership and role changes, invitations, user-lifecycle and policy changes) by actor, entity, time, and event type. The surface shall read the existing immutable audit records rather than introduce a parallel log.

## Rationale

Administrators of a shared deployment need to answer who changed what and when, especially for membership, role, and policy changes. A read surface over the existing immutable shared.audit record satisfies this without a second logging system (PLAT-241).

## Traceability

- IMPLEMENTS → GITHUB_ISSUE `1947`
