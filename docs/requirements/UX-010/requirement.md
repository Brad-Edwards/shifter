---
id: UX-010
title: "WCAG 2.2 Level AA conformance"
status: DRAFT
type: NON_FUNCTIONAL
priority: MUST
created_at: 2026-05-09T04:37:45.763376Z
updated_at: 2026-05-09T04:37:45.763376Z
---

# UX-010: WCAG 2.2 Level AA conformance

## Statement

Every public surface of the platform shall conform to WCAG 2.2 Level AA. Conformance shall be verified by automated tooling on every pull request and by a manual audit before any release that introduces a new surface or significantly changes an existing one. Failures shall block release until remediated or explicitly waived with documented rationale.

## Rationale

No accessibility pass has ever been done on this codebase. WCAG 2.2 AA is the standard baseline for accessible web applications, and treating it as table stakes for an OSS project that wants any kind of public adoption is the floor, anything less excludes users with disabilities and creates legal exposure for adopters.
