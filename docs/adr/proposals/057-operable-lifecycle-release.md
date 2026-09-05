# ADR-057: Public lifecycle contracts and recoverable releases

- Status: proposed (2026-09-05)
- Review: #2080; findings F04, F05, F08–F12
- Extends: ADR-040 public API and ADR-043 immutable worker operations

## Context

Internal operation generations, launch intents and result application have strong
foundations. External clients still need explicit retry and outcome contracts.
An installable release also needs evidence for upgrades, recovery, cleanup and
supported capacity; passing unit tests does not supply that evidence.

## Proposed decision

Expose authorized lifecycle requests and operation status through the versioned
public API. Bind caller retry keys to deployment, actor, action and immutable
intent. Replays reauthorize and return the same operation; a different intent
under the same key conflicts before effects. The server retains execution
generation authority. Reuse the existing ledger, launch intent, result and event
mechanisms; do not create a parallel orchestration state machine.

Distinguish admitted, executing, observed-ready, failed, cleanup-pending, unknown
and independently verified terminal outcomes. Preserve old-generation provider,
resource and credential identity through upgrades and pack retirement. A timeout
is not proof of failure or successful destruction. Cancellation and containment
are durable intents with observable completion and residual obligations.

Qualify the GCP release with a fresh project, declared artifacts, supported user
journeys, measured capacity, failure injection, upgrade/rollback and a restore
drill. Record RPO/RTO and dependency-outage behavior. Reconcile restored state
against provider reality before enabling mutating work. Operator runbooks name
responsibility for alerts, cleanup and cost. Release quality/security decisions
apply to exact deployed artifacts; a successful scanner invocation does not
override its failed quality gate.

## Alternatives and consequences

Reject promising an installation SLA from the local backend's ten-minute journey.
Reject both an unbounded generic workflow engine and one-off client scripts that
reimplement authorization or cleanup. Durable operations add retained state and
compatibility obligations; that cost is necessary for safe external use.

## Adoption evidence and enforcement

Contract tests cover lost responses, duplicates, changed intent, revoked actors,
stale generations and operation lookup. Real PostgreSQL tests cover contention.
Live drills cover worker/provider loss, old-plan teardown, partial restore,
quarantine and independently discovered residual resources. Publish release
evidence with exact commits/digests, commands, environment, observations and limits.
Wire critical API/browser/security checks to existing CI owners. Add no redundant
test lane or manual checklist pretending to enforce runtime behavior.
