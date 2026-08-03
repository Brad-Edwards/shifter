---
id: PLAT-202
title: "Per-Range LLM Access Management"
status: DRAFT
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-04-16T22:49:32.549002Z
updated_at: 2026-04-16T22:49:32.549002Z
---

# PLAT-202 — Per-Range LLM Access Management

## Statement

The platform shall provision per-range access to external LLM and agentic-tool APIs, with shardable allocation across models, clouds, and accounts. Shard assignment, credential plumbing, and endpoint routing shall be a first-class platform capability rather than an out-of-band operator script. Shard strategy shall be configurable per scenario or per event, informed by event capacity declarations (CTF-908) and planned by capacity-aware provisioning (PLAT-201).

## Rationale

Scenarios increasingly assume agentic tooling inside participant ranges (e.g. Claude Code inside Kali). At Ottawa BSides this was handled by an SSM fan-out script (scripts/polaris-aws-range/apply_kali_bedrock_shard.py) that sharded credentials across AWS accounts and Bedrock inference profiles based on user_id % 8. That pattern is brittle: every capacity shift, model availability change, or account reshuffle requires a new bespoke script. Moving the capability into the platform lets scenario authors express "this range needs agentic-model access" and have the platform handle allocation.

## Traceability

- DOCUMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#681` (PLAT-202: Per-Range LLM Access Management)
