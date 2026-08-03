---
id: PLAT-204
title: "Scenario Catalog and Editor"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-05-09T05:11:30.172202Z
updated_at: 2026-05-09T05:11:30.186097Z
---

# PLAT-204 — Scenario Catalog and Editor

## Statement

The platform shall provide a scenario catalog and staff-only scenario editor for creating, validating, listing, disabling, and deleting scenario definitions used by range and experiment workflows. Scenario definitions shall be schema-validated and shall enforce visibility and access metadata before provisioning.

## Rationale

Scenario catalog/editor code exists and is used by range, CTF, and experiment workflows, but GC only had a future scenario expressiveness constraint rather than the implemented product capability.

## Traceability

- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#402` (Smoke test: Scenario Editor end-to-end)
- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#403` (Create Scenario Editor documentation)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/services.py` (Scenario editor service layer)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/views.py` (Scenario editor web/API views)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenarios/schema.py` (Scenario definition schema validation)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenarios/registry.py` (Scenario registry and access filtering)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/models/scenarios.py` (Scenario and scenario metadata models)
- TESTS → TEST `shifter/shifter_platform/tests/scenario_editor/test_services.py` (Scenario editor service tests)
- TESTS → TEST `shifter/shifter_platform/tests/cms/test_scenario_schema.py` (Scenario schema tests)
- IMPLEMENTS → PULL_REQUEST `733` (Add CyberScript docs and additional scenario editor script validation)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_common.py` (Scenario editor service shared helpers)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_crud.py` (Scenario editor create/update/delete services)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_metadata.py` (Scenario editor metadata services)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_persistence.py` (Scenario editor persistence helpers)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_validation.py` (Scenario editor validation services)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/scenario_editor/_yaml.py` (Scenario editor YAML export services)
