# BigRAE adoption review

Review issue: [#2080](https://github.com/Brad-Edwards/shifter/issues/2080).
Reviewed on 2026-09-05 at `dev` commit
`b9b82681818fed7da26fcedaa93d37586bc14c74`.

**Keep the architecture and finish its operational contracts. Shifter is a
substantial, increasingly disciplined backend, but it is not yet a demonstrated,
repeatably adoptable BigRAE release.** GCP/GCE is the first qualification target.
AWS remains a separate qualification track. A rename is not the release gate.

The intended first product is an operated, dedicated single-customer deployment
with internal organizations/workspaces, authenticated users, and isolated ranges.
It is not a claim that unrelated customers can safely share one control plane.
Existing organization/workspace authorization remains useful inside that boundary.

## Reading the review

| Artifact | Purpose |
| --- | --- |
| [Findings](findings.md) | Source-backed assessment, consequences and remedies |
| [Range and agent security](sandbox-security.md) | Threat model, sandbox choices, escape detection, response and modest-cost options |
| [Backlog](backlog.md) | Milestones, dependencies and disposition of every baseline open issue |
| [Verification](verification.md) | Commands, results and material limits of this assessment |
| [ADR proposals](../../adr/proposals/README.md) | Four proposed decisions and exact existing-ADR clarifications |

## Judgment by dimension

| Dimension | Judgment | What changes adoption confidence |
| --- | --- | --- |
| Design | Retain the Django domain structure and separate provisioner | Make admission, operation identity, effects, observations and cleanup distinguishable to clients |
| Code quality | Strong local discipline; substantial maintenance surface | Remove retired callers and scenario coupling; maintain behavioral tests and clear public facades |
| Architecture | Good boundaries with documented implementation gaps | Resolve CTF tenancy contradiction, release-qualified capability claims and deployment authority |
| Security | Useful preventive controls; release assurance incomplete | Close applicable scanner/configuration findings and prove containment, credentials, monitoring and response together |
| Fitness for purpose | Credible GCP foundation; limited scenario support | Qualify exact released scenarios through participant use, failure, reset where supported, and complete teardown |
| Operations | Extensive automation; too much evidence still anecdotal or deferred | Fresh-project installation, upgrade, restore, load and failure drills on the declared release |
| Roadmap | Too many historical tracking issues obscure the critical path | One small adoption gate; explicit dispositions and native dependencies; no new feature program masquerading as stabilization |

## What is worth preserving

The enforced service boundaries, native `shared` contracts, exact producer-version
checks, fail-closed capability checks, digest-verified content ingestion,
generation-fenced operation inputs/results, launch intents, transactional range
event outbox, owner-first remote access, scoped API tokens, PostgreSQL semantics
lane and repository guardrails are material assets. They are reasons to stabilize
this implementation rather than rewrite it or introduce microservices to make the
directory structure appear cleaner.

The previous REV1 review is historical evidence. Several of its most serious
findings have been addressed: installed-app classification, API publication,
provisioner operation boundaries on the RAES path, PostgreSQL CI and provenance
controls have progressed. Its old finding list must not be copied forward as a
description of today's code.

## OpenRAE interpretation

[Hub #3](https://github.com/OpenRAE/hub/issues/3) and
[Hub #29](https://github.com/OpenRAE/hub/issues/29) place semantics, contracts and
conformance in RAES; content discovery and reusable assets upstream; the complete
personal backend in LilRAE; and organizational operations in BigRAE. The local
ten-minute journey is not BigRAE's installation SLA. Its useful principles still
apply: released artifacts, inspectable results, bounded changes and verified
cleanup.

Pending [#2075](https://github.com/Brad-Edwards/shifter/issues/2075) and
[PR #2079](https://github.com/Brad-Edwards/shifter/pull/2079) already replace
ADR-033 with ADR-053's two-surface ownership model. This review reserves ADR-053
for that work and proposes ADR-054 through ADR-057. It does not change repository,
package or artifact identities. The later Hub comment permits preparation under
current names while reserving new durable identities/releases and breaking
changes for their own decisions.

## First adoption gate

An independent operator must be able to install one declared GCP release,
authenticate, import a released pack, see exact support or rejection, launch it,
use its participant path, inspect evidence, exercise failure/recovery, and remove
all run-owned resources and credentials. The same release must survive a measured
upgrade/restore drill and a bounded event load test. Its security controls must
have positive and negative tests, an external evidence destination, and an
operator who can respond to an alert.

Select a small infrastructure canary and one useful end-to-end scenario as the
initial cohort. POLARIS is a candidate because it exercises real identity,
desktop, content and agent dependencies; it is not declared qualified by this
review. Select immutable upstream releases and record unsupported capabilities.
Do not require all infrastructure kits, all participant-control mechanisms,
arbitrary uploaded code, a service mesh, a SIEM, or AWS parity to pass this gate.

## Scope and limits

The inventory covers 4,121 tracked files, all 282 baseline open issue bodies,
their 134 comments, 39 existing blocking relationships, 34 milestones, 52 ADR
records, and the three open PRs. Source inspection follows execution and trust
boundaries across every major subsystem; it is not a claim of line-by-line proof
of every file. Largest-file metrics exclude tests but still include generated
contracts, migrations and vendor code; those are distinguished in the findings.

This is a repository and design assessment with local verification. It does not
include a new live cloud deployment, destructive escape testing, a penetration
test, a release certification or an independent maintainer review. Intentionally
vulnerable scenario behavior is not a platform vulnerability unless it crosses
the declared boundary. Recommendations are proposed work, not assertions that
the safeguards already exist.
