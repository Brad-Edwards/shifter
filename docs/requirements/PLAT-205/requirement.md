---
id: PLAT-205
title: "Experiment Run Orchestration"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-05-09T05:11:30.209115Z
updated_at: 2026-05-09T05:11:30.223635Z
---

# PLAT-205 — Experiment Run Orchestration

## Statement

The platform shall provide experiment management for repeatable scenario runs, including scripts or prompts assigned to scenario instances, controlled run fan-out, lifecycle states, range provisioning integration, artifact capture, and status/event handling.

## Rationale

Experiment management is implemented as a first-class CMS subsystem, but the GC requirement set had no experiment-management capability requirement.

## Traceability

- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#399` (Add Research VPC and Experiment Management)
- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#466` (Experiment creation bypasses staff_only and disabled scenario restrictions)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/models.py` (Experiment, script, run, and artifact models)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/consumers.py` (Experiment WebSocket consumers)
- TESTS → TEST `shifter/shifter_platform/tests/cms/experiments/test_orchestrator.py` (Experiment orchestrator tests)
- TESTS → TEST `shifter/shifter_platform/tests/cms/experiments/test_views.py` (Experiment view tests)
- IMPLEMENTS → PULL_REQUEST `780` (Experiment creation enforces staff only and disabled restrictions)
- TESTS → TEST `shifter/shifter_platform/tests/cms/experiments/test_view_flows.py` (Experiment view branch-flow tests)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/orchestrator/coordinator.py` (Experiment run orchestration (coordinator))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/orchestrator/execution_plan.py` (Experiment run execution-plan construction)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/orchestrator/run_provisioning.py` (Experiment run range provisioning)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/orchestrator/run_dispatch.py` (Experiment run command dispatch)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/orchestrator/run_artifacts.py` (Experiment run artifact collection)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/views/_experiments.py` (Experiment lifecycle views (list/create/detail/start/cancel))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/views/_scripts.py` (Experiment script-asset views (list/upload/delete))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/views/_downloads.py` (Experiment bundle/artifact download views)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/experiments/views/_ajax.py` (Experiment AJAX views (scenario instances))
