---
id: GEN-002
title: "Executable Architecture Governance"
status: ACTIVE
type: NON_FUNCTIONAL
priority: MUST
wave: 1
created_at: 2026-05-09T05:11:30.016736Z
updated_at: 2026-05-09T05:11:30.051524Z
---

# GEN-002 — Executable Architecture Governance

## Statement

The repository shall maintain executable architecture governance for accepted architecture constraints. Accepted ADRs with enforceable impact shall be represented in a machine-readable registry, time-bounded exceptions, and automated checks that run locally and in CI; workflow or test skip mechanisms shall not bypass architecture conformance.

## Rationale

The repo now has guardrail enforcement in ADR registry, adr_guard, CI, hooks, and agent policy. This is a governance NFR, not implementation guidance for feature delivery.

## Traceability

- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#689` (Refactor CI and guardrail automation god files)
- CONSTRAINS → ADR `ADR-001` (Cross-layer access goes through service boundaries)
- CONSTRAINS → ADR `ADR-002` (Guardrail changes must remain documented)
- CONSTRAINS → ADR `ADR-003` (ADR enforcement is a required architecture gate)
- CONSTRAINS → ADR `ADR-004` (Use stack-appropriate off-the-shelf policy tooling where it fits)
- CONSTRAINS → ADR `ADR-006` (Kubernetes workloads must meet Pod Security Standards)
- IMPLEMENTS → CONFIG `docs/adr/index.yaml` (Machine-readable ADR registry)
- IMPLEMENTS → CONFIG `docs/adr/exceptions.yaml` (Time-bounded ADR exceptions)
- IMPLEMENTS → CODE_FILE `scripts/adr_guard/adr_guard.py` (ADR guard architecture policy runner)
- IMPLEMENTS → CONFIG `.gc/plan-rules.md` (Ground Control plan rules for architecture checks)
- IMPLEMENTS → CONFIG `.github/workflows/_quality.yml` (CI architecture and quality gate)
- IMPLEMENTS → CONFIG `.pre-commit-config.yaml` (Local guardrail pre-commit hooks)
- TESTS → TEST `scripts/adr_guard/tests/test_adr_guard.py` (ADR guard regression tests)
- DOCUMENTS → DOCUMENTATION `docs/adr/README.md` (ADR enforcement documentation)
- DOCUMENTS → DOCUMENTATION `docs/adr-enforcement-plan.md` (ADR enforcement design plan)
- IMPLEMENTS → PULL_REQUEST `947` (ADR enforcement audit and guardrail backfill)
- IMPLEMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#915` (deploy: single source of truth for deployment mode and portal sizing)
- IMPLEMENTS → CONFIG `.github/workflows/_shifter-platform.yml` (AWS portal deploy workflow topology enforcement)
- IMPLEMENTS → CODE_FILE `scripts/portal_deploy/portal_deploy.py` (Terraform-sourced portal deploy topology helper)
- TESTS → TEST `scripts/portal_deploy/tests/test_portal_deploy.py` (Portal deploy topology and ASG verification tests)
- IMPLEMENTS → PULL_REQUEST `964` (fix: unify portal deploy mode source)
- IMPLEMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#927` (test: adopt a boundary-mock policy + lint to stop topology-coupled tests)
- IMPLEMENTS → PULL_REQUEST `973` (test: add boundary mock policy guard)
- IMPLEMENTS → CODE_FILE `scripts/adr_guard/boundary_mock_baseline.json` (Boundary mock legacy baseline)
- CONSTRAINS → ADR `ADR-019` (Tests mock external boundaries, not first-party topology)
- DOCUMENTS → DOCUMENTATION `shifter/shifter_platform/documentation/docs/technical/dev/adr-enforcement.md` (Developer ADR enforcement documentation)
- IMPLEMENTS → CODE_FILE `scripts/quality_ownership/contract.py` (Production-path quality-ownership contract parser (#1530))
- IMPLEMENTS → CODE_FILE `scripts/quality_ownership/classify_paths.py` (Fail-closed changed-path classifier for the _quality.yml paths job (#1530))
- IMPLEMENTS → CONFIG `.github/quality-path-filters.yaml` (Versioned production-path quality-ownership contract (#1530))
- TESTS → TEST `scripts/adr_guard/tests/test_quality_path_ownership.py` (Quality-path-ownership gate tests (#1530))
- IMPLEMENTS → GITHUB_ISSUE `1530` (REV1 Testing: enforce production-path ownership in routed CI)
- TESTS → TEST `scripts/adr_guard/tests/test_deploy_workflow.py` (TestSonarScannerIdentity / TestExpressionOperandCoverage (ADR-003-R7, #1874): a workflow skip condition cannot bypass the SonarCloud conformance gate)
