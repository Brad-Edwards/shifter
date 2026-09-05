# Proposed BigRAE adoption decisions

These decisions accompany [review #2080](https://github.com/Brad-Edwards/shifter/issues/2080).
They are **proposed**, not accepted architecture or implemented controls. They
do not change active guardrails or grant exceptions. `docs/adr/index.yaml`
continues to contain the operative decisions; adopting each proposal requires
updating that registry, the referenced prior rules and their enforcement in the
same implementation change.

ADR-053 remains reserved for #2075 / PR #2079. Review its two-surface ownership
decision first; this branch does not duplicate or merge that pending change.

| Proposal | Decision | Existing decisions to reconcile on adoption |
| --- | --- | --- |
| [ADR-054](054-single-customer-deployment.md) | Dedicated customer deployment and internal authority | Clarify ADR-046-R7 and ADR-052-R6 as the pre-migration CTF state; ADR-051 event binding takes effect with its migration; retain distinct event authority |
| [ADR-055](055-scenario-qualified-materialization.md) | Released scenario qualification determines the materialization envelope | Clarify ADR-024/034 with pending ADR-053; retain RAES ownership, exact wire-version checks and admission semantics |
| [ADR-056](056-range-agent-security.md) | Range/agent containment includes external detection and response | Clarify ADR-030/039 containment and ADR-008/017 credential/network rules; replace #1295's unconditional service-mesh prerequisite with a threat-driven choice |
| [ADR-057](057-operable-lifecycle-release.md) | Public lifecycle and recoverable release evidence | Extend ADR-040 public API and ADR-043 worker contract; retain durable operation ownership and generation fencing |

No other ADR should be superseded merely to remove historical wording. Retain
the service-boundary, native contract, provenance and enforcement decisions.
ADR-033's supersession belongs to ADR-053. For each adoption, list precise rule
edits, migration sequencing, applicable behavioral tests and rollout evidence;
do not add unenforced normative rules with no owner.
