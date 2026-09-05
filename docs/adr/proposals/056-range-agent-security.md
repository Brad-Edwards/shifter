# ADR-056: Range containment includes detection and response

- Status: proposed (2026-09-05)
- Review: #2080; findings F06–F09
- Design detail: [range and agent security plan linked from review #2080](https://github.com/Brad-Edwards/shifter/issues/2080)

## Context

Ranges intentionally host hostile activity, sometimes with guest administrator
authority. Existing finite network escape probes do not establish continuous
monitoring or safe model/tool authority. A large mandatory security stack would
make the small deployment difficult to operate without proving better outcomes.

## Proposed decision

Treat each participating guest as potentially compromised. Declare boundaries
between tools, containers, guests, ranges, management, provider IAM and telemetry.
Use the GCE VM boundary initially, least-authority identities, scoped connectivity,
resource budgets and scenario-required exceptions. Preserve controls around the
range while permitting intended activity inside it. A mesh, SIEM or additional
sandbox runtime is selected only for a demonstrated unmet requirement.

Required security profiles include outside-guest cloud/network evidence, one
qualified host collection path where claimed, sensor silence/drop monitoring,
authenticated bounded append-only export, and tested incident response. Host-root
compromise can subvert host-local telemetry; disclose that limit. No sensor
claims complete detection of provider hypervisor escapes.

Keep model/provider credentials and privileged operator tools outside participant
authority where possible. Use range/generation-scoped broker policy with explicit
tools/models/destinations, budget, concurrency, deadlines and revocation. Treat
model output and scenario content as untrusted. Interactive permission settings
are not a sandbox.

Correlate security evidence to immutable range generations. Quarantine and revoke
through existing lifecycle services, preserve evidence and retain cleanup work.
Start with operator-confirmed containment; qualify automation before enabling it.
Never silently weaken required telemetry to admit a workload during an outage.
Offer GCP-native and modest OSS options behind the existing evidence contract.

## Alternatives and consequences

Pure guest self-reporting is insufficient. Deploying several overlapping sensors,
an always-on search cluster or a service mesh by default adds maintenance and
cost without an automatic security improvement. gVisor, Kata and Firecracker
remain workload-qualified options, not commitments to extra production backends.
Detection has operating costs and requires a named responder.

## Adoption evidence and enforcement

Reuse #1295 for containment design, #1019 for durable telemetry, #1020 for live
regression and #681/#1586 for credential authority. Record exact network/identity
invariants and affected ADR rules on adoption. Test boundary denial with positive
controls, sensor loss/drop, log flood, stale attribution, canary alerts, quarantine
failure, credential revocation and recovery. Report observed latencies, limits,
cost per scenario-hour and operator instructions. Keep exploit details in private
security reporting; public acceptance tests can use bounded synthetic probes.
