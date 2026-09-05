---
id: UX-050
title: "Automated accessibility testing in CI"
status: DRAFT
type: NON_FUNCTIONAL
priority: MUST
created_at: 2026-05-09T04:39:06.880538Z
updated_at: 2026-05-09T04:39:06.880538Z
---

# UX-050: Automated accessibility testing in CI

## Statement

Automated accessibility testing (for example axe-core or equivalent) shall run on every pull request against representative pages. New violations introduced by a pull request shall fail the build. Existing violations shall be tracked in a baseline that can only shrink, not grow.

## Rationale

A11y tooling catches a substantial fraction of WCAG failures automatically. Without a CI gate, regressions are inevitable and the baseline drifts. The "baseline can only shrink" pattern lets the project tackle existing debt incrementally without blocking other work.

## Traceability

- DOCUMENTS → ADR `docs/adr/index.yaml` (ADR-055 browser-regression and non-growing-baseline contract)
- DOCUMENTS → DOCUMENTATION `docs/architecture/continuous-accessibility-enforcement-preflight-713.md` (Tool selection, CI cadence, coverage inventory, failure semantics, and baseline algorithm)
- DOCUMENTS → GITHUB_ISSUE `713` (Architecture decision for continuous accessibility enforcement)
