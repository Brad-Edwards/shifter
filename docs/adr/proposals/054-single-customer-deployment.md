# ADR-054: Dedicated customer deployment with explicit internal authority

- Status: proposed (2026-09-05)
- Review: #2080; finding F03
- Depends on: ADR-053 ownership decision (#2075)

## Context

The first BigRAE product is an operated single-customer SaaS deployment on GCP,
with GCE ranges. Current organizations/workspaces and CTF authority are useful
internal scopes, but some catalog, settings and credential state is deployment
global. ADR-046/052 describe global CTF events while ADR-051 describes a workspace
binding that is not in the baseline event model.

## Proposed decision

One deployment is one customer security and administration boundary. Supported
deployment artifacts declare its identity, version, provider, trust domains,
configuration and resource limits. Unrelated customers do not share a control
plane, secret authority or data stores under this release claim. Provider project,
network and identity placement must be recorded and tested; a label alone is not
an isolation boundary. Internal workspaces and events retain their explicit
authorization checks, even within a dedicated deployment.

Deployment-local services own admission, policy, secrets, lifecycle execution,
audit and persisted results. External clients use the versioned public API and
receive only authorized capabilities/status/results. External discovery or
coordination must not bypass these checks or become a second authority for cloud
effects. Define which operations remain available during IdP, registry or model
outages; dependency loss never invents permission or completion.

Event ownership/delegation and workspace membership remain different concepts.
Adopt ADR-051's immutable binding with #2048's migration and authorization tests.
Until then, explicitly describe the existing deployment-global model. On adoption,
edit ADR-046-R7 and ADR-052-R6 to state this transition rather than retaining
contradictory unconditional rules. A workspace binding must not silently grant
event participation, organization or secret access.

## Alternatives and consequences

Reject adding unrelated-customer multitenancy before there is a product need and
a complete state/identity isolation design. Dedicated deployments cost more per
customer but constrain authority and simplify recovery and incident ownership.
Do not flatten internal authorization because the deployment has one customer.
Do not split the modular monolith to express deployment ownership.

## Adoption evidence and enforcement

The adoption issue owns an authority matrix and exact ADR registry edits. Tests
cover session and scoped-token access, workspace archival/membership loss,
cross-event access, remote session revocation and audited administrator override.
Deployment tests verify provider identity/network separation and denied paths.
Migration tests cover old events and preserve existing participant authority.
Record an owner for each shared service and effective IAM role. Add guard checks
only where a real invariant can be checked; runtime claims require runtime tests.
