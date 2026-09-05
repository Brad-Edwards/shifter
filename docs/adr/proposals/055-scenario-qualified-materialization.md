# ADR-055: Scenario-qualified, released materialization envelopes

- Status: proposed (2026-09-05)
- Review: #2080; findings F01, F02, F10, F11
- Clarifies: ADR-024/034 and pending ADR-053; retains RAES contract ownership

## Context

The backend correctly rejects many unsupported shapes, but authored capability
declarations do not demonstrate working scenarios. The pinned upstream versions
lag newer releases, and a stable profile identity is different from the exact
deployed binary. GCP supports the native RAES path; AWS parity is outstanding.

## Proposed decision

Publish a release support matrix with exact backend build/image digests, provider,
configuration/profile digest, RAES and pack versions, resource bounds and supported
operations. Admit a request only if its complete requirements fit that matrix.
Unsupported versions/shapes fail before provider effects. Do not claim universal
OpenRAE conformance or participant/evaluator/observation capabilities from a
provisioning-only manifest.

Select a small released infrastructure canary and one useful full scenario for
the first GCP cohort. Expansion is demand-driven: a concrete scenario requires a
capability; implementation and adverse tests land; upstream conformance and
independent participant/lifecycle observations prove it; the supported matrix
then expands. #1949's remaining kits and #1967–#1969's additional participant
control work remain subsequent scope unless selected scenario requirements
make them necessary.

Use released upstream artifacts without sibling checkouts or mutable branches.
Evaluate current RAES/pack releases as a compatibility change, not an automatic
latest-version bump. Preserve an old generation's cleanup capability through an
upgrade, or refuse the upgrade with an actionable migration path. Scenario
content, walkthroughs, evaluators and scenario-specific MCPs belong with their
released content owner. The backend owns reusable lifecycle, credentials,
transfer, network, execution and evidence mechanisms.

## Alternatives and consequences

Reject claiming a broad envelope from schema parsing alone. Reject implementing
every possible kit before one complete useful scenario works. Narrow supported
profiles reduce breadth but provide evidence operators can rely on. Additional
provider/runtime support is a separate qualification, not a naming convention.

## Adoption evidence and enforcement

Store machine-readable release manifests and qualification results tied to all
relevant digests. Verify admission/no-effects negatives, deterministic planning
where specified by RAES, observed guest readiness, participant use, failure,
supported reset/pause, cancellation and residual-free teardown. Conformance
results name the actual upstream suite/version and capability profile. Load and
resource limits are measured. Public docs and API claims are checked against the
same manifest; no manually maintained second capability registry.
