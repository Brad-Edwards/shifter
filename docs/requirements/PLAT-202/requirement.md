---
id: PLAT-202
title: "Per-Range LLM Access Management"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-04-16T22:49:32.549002Z
updated_at: 2026-09-07T05:22:09Z
---

# PLAT-202: Per-Range LLM Access Management

## Statement

The platform shall provision per-range access to external LLM and agentic-tool APIs, with shardable allocation across models, clouds, and accounts. Shard assignment, credential plumbing, and endpoint routing shall be a first-class platform capability rather than an out-of-band operator script. Shard strategy shall be configurable per scenario or per event, informed by event capacity declarations (CTF-908) and planned by capacity-aware provisioning (PLAT-201).

## Rationale

Scenarios increasingly assume agentic tooling inside participant ranges (for example Claude Code inside Kali). At Ottawa BSides this was handled by an SSM fan-out script (scripts/polaris-aws-range/apply_kali_bedrock_shard.py) that sharded credentials across AWS accounts and Bedrock inference profiles based on user_id % 8. That pattern is brittle: every capacity shift, model availability change, or account reshuffle requires a new bespoke script. Moving the capability into the platform lets scenario authors express "this range needs agentic-model access" and have the platform handle allocation.

## Traceability

- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#681` (PLAT-202: Per-Range LLM Access Management)
- IMPLEMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#2118` (Policy catalog and shared access contracts)
- IMPLEMENTS → DOCUMENTATION `docs/architecture/model-access/architecture.md`
- IMPLEMENTS → DOCUMENTATION `docs/architecture/model-access/canonical-json-v1-vector.json`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/models.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/core_models.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/sharing_models.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/catalog.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/allocation.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/policy.py`
- IMPLEMENTS → CODE `shifter/shifter_platform/shared/model_access/provider.py`
- IMPLEMENTS → CODE `shifter/installation/model_access.py`
- IMPLEMENTS → CONFIG `shifter/installation/pyproject.toml`
- IMPLEMENTS → CODE `shifter/installation/loader.py`
- IMPLEMENTS → CODE `shifter/installation/render.py`
- IMPLEMENTS → CONFIG `shifter/installation/published_contract/model-access-policy.v1.schema.json`
- IMPLEMENTS → CONFIG `shifter/shifter_platform/config/_model_access_settings.py`
- IMPLEMENTS → CODE `scripts/gcp/render_runtime_env.py`
- IMPLEMENTS → CODE `scripts/bootstrap/aws_eks.py`
- TESTS → TEST `shifter/shifter_platform/tests/shared/model_access/test_contract.py`
- TESTS → TEST `shifter/shifter_platform/tests/shared/model_access/test_allocation.py`
- TESTS → TEST `shifter/shifter_platform/tests/shared/model_access/test_provider.py`
- TESTS → TEST `shifter/shifter_platform/tests/shared/model_access/test_schema_publication.py`
- TESTS → TEST `shifter/shifter_platform/tests/config/test_model_access_settings.py`
- TESTS → TEST `shifter/installation/tests/test_model_access.py`
- TESTS → TEST `scripts/gcp/tests/test_render_runtime_env.py`
