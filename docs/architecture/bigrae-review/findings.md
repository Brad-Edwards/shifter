# Findings at the review baseline

Baseline and limitations: [review](README.md) and [verification](verification.md).
Priorities describe adoption urgency, not CVSS: P1 blocks the first applicable
release claim; P2 must be resolved or explicitly excluded by the release profile;
P3 is optional improvement. Code paths below are relative to the repository at
`b9b82681818fed7da26fcedaa93d37586bc14c74`.

## F01 — A backend declaration is not a qualified scenario envelope (P1)

`shared/raes/manifest.py` publishes a **provisioning-only** profile with VM/switch,
Linux/Windows, IPv4, file/directory, bounded account and Active Directory support.
It deliberately omits mail/dataset and full participant-runtime, evaluator and
observation claims. `realization.py` supplies another validated configuration;
`composition_envelope.py` rejects unsupported shapes. Those are good controls,
but the two declarations are application-authored data, not independent runtime
proof. `max_total_nodes=None` is also not an operational capacity promise.

The platform pins `raes==2.0.0` and `raes-env-packs==3.1.0`. At review time the
upstream latest releases are [RAES v3.5.0](https://github.com/OpenRAE/rae/releases/tag/v3.5.0)
and [env-packs v4.0.2](https://github.com/OpenRAE/env-packs/releases/tag/v4.0.2).
Pinning an older supported contract is not nonconformance, and a blind upgrade
would be unsafe. A release compatibility assessment is required. The provisioner
reader correctly rejects unsupported producer versions; #1937 already owns its
long-term public accessor decision.

`shifter_backend_apparatus()` and the published manifest normalize identity to
`0.0.0`; the package metadata remains `0.1.0`. Stable profile identity must be
distinguished from the exact deployed implementation/image and configuration in
the release evidence. An artifact-profile digest must not stand in for a binary
digest.

**Remedy:** ADR-055; select exact released cohort artifacts, contract versions,
provider/image/configuration digests and resource bounds. Run released upstream
conformance plus independent guest and lifecycle probes. An envelope expansion
requires a concrete scenario demand, implementation, adverse/unsupported tests,
and observed effects. Reuse #1937 and a bounded slice of #1949. Keep #1967–#1969
and #2050/#2054 outside the initial claim unless a selected scenario requires them.

## F02 — AWS RAES-native parity is not implemented (P1 for AWS; follow-on for GCP)

`cms/services/_raes_range_create.py` admits only the `gce` RAES realization
adapter. `shared/raes/operation_input.py` admits `gce`/`gdc`, and the normal
live-fire purpose excludes GDC. AWS still uses the legacy Terraform `range`
family, whose ownership-bearing input and cleanup behavior differ. This is
correctly tracked by #2069; #2043 cannot prove AWS RAES parity before that work.

**Remedy:** GCP-first capability/release matrix. Preserve fail-closed admission,
and do not present installation-bundle support as RAES realization parity. AWS
qualification depends on #2069, #1894 and its EKS/deployment evidence. Retain AWS
maintenance while preventing it from expanding the first GCP release gate.

## F03 — Deployment tenancy and CTF authority need one unambiguous contract (P1)

The workspaces domain has persisted memberships, closed operations, locked launch
authorization and explicit range bindings. CTF has its own event ownership,
delegation and audited platform-superuser override. These are useful distinctions.
They do not isolate deployment-global catalog, credentials and settings between
unrelated customers.

ADR-046-R7 and ADR-052-R6 say CTF events remain deployment-global; accepted
ADR-051-R2 says every event gains an immutable workspace binding. On the reviewed
branch `ctf/models/event.py` has no such field. Pending #2048 / PR #2074 is the
implementation context. Leaving all three prose rules apparently current invites
different authority checks at different entrypoints.

**Remedy:** ADR-054 explicitly defines one customer security boundary per
deployment, retains workspace and event permissions, and specifies that ADR-051's
event binding becomes operative only through its migration. Clarify ADR-046 and
ADR-052 in that same implementation; never infer event access from workspace
membership. Add session/token, archived-workspace, cross-event, remote-access and
administrator-override negative cases. Reuse the existing authorization services.

## F04 — Internal operation safety is ahead of the public client contract (P1)

`engine/launch_intents.py`, `engine/operation_inputs.py`, `shared/operation_envelope.py`
and the operation-result applier provide durable generations and fenced effects.
The RAES provisioner reads immutable inputs and appends results rather than
writing the domain tables directly. This is a major improvement over REV1.

`mission_control/api/ranges.py::LaunchRangeView` creates a server request through
CMS and returns it; there is no client retry-key contract in the launch
serializer. A client that loses the response cannot replay its request to recover
the original result. Internal `engine/services/_raes_range.py::create_raes_range`
reuses an existing `request_id` after checking backend/workspace/egress and access
bindings, but does not compare the whole compiled plan and content/artifact
bindings. This is not a demonstrated cross-user exploit: that seam is internal.
It is an incomplete same-key/different-intent contract.

**Remedy:** add bounded, actor/deployment/action-scoped public retry identity with
immutable intent comparison, operation/status lookup, cancellation and cleanup
outcomes. Replays of different intent conflict before effects; reauthorization
still occurs. Keep server-owned execution generations distinct from a caller's
retry key. Version public changes under ADR-040 and retain ADR-043 as the one
worker contract. Do not introduce a second operation ledger.

## F05 — Cleanup and recovery need observed release evidence (P1)

The GCP RAES path has failure cleanup, generation fencing and reconciled cancel
intent. #1893 is an early-abort efficiency improvement, not proof that GCP has no
cancel mechanism. AWS's separate cancellation gap is #1894.

`raes_range_ops.py::run_raes_range_destroy` parses the stored plan using the
current accepted producer version and loads current backend configuration before
destroy. A later contract/configuration cutover therefore needs an explicit
old-generation cleanup compatibility strategy. Credential, placement, egress and
resource ownership must remain resolvable after pack retirement or upgrades.
Issue #1919 is an existing ordering defect to revalidate, not automatically a new
finding on every destroy path.

**Remedy:** release-bound failure matrix: kill worker/launcher/applier; lose a
provider response; retry duplicate/out-of-order results; cancel during each phase;
expire leases; remove a registry entry; retire a pack; upgrade with old ranges;
lose database/message/secret access; discover a resource after apparent teardown.
Only independent residual inventory permits a clean terminal claim. Retain
cleanup-pending/unknown obligations and backoff. Preserve evidence before deletion.

## F06 — Range containment tests exist; continuous escape monitoring does not (P1)

The existing `run_range_escape_validation` command and
`gcp_range_cell_escape_checks.py` cover important cross-range, management,
metadata and egress boundaries with positive controls and fail-closed skips.
The runbook explicitly excludes general UDP probing. These are finite network
probes, not a kernel-escape detector or proof of all possible paths.

The reviewed manifests/provisioner contain no maintained Falco/Tetragon runtime
sensor pipeline or continuous escape-response policy. GCP platform flow logs,
queue alarms, worker heartbeats and application audit do exist. They are useful
incumbents but do not establish range-host sensor coverage, tamper alerts,
out-of-range evidence durability or incident containment.

**Remedy:** the [security plan](sandbox-security.md), ADR-056 and #1019/#1020.
Use outside-boundary network/cloud observations plus a supported host sensor,
monitor sensor silence/drop rates, correlate deployment/range/generation, and
exercise quarantine and credential revocation. Do not claim an uncompromised
host because an agent inside it reports success.

## F07 — Agent credentials and tools need a range-scoped authority budget (P1 when enabled)

Guest agents are already real workloads: the Packer autostart script invokes an
agent with its interactive permission prompts disabled. That setting is not OS
containment. The GCP model-credential lifecycle has per-range and shared-key
deployment modes; a copied key is not an independent range authorization
principal. Model identity, request budget, destination and revocation must be
part of the selected profile. The privileged operator MCP intentionally has
secret retrieval and infrastructure authority; its local policy engine is not
a participant sandbox.

**Remedy:** qualify least-authority model/tool access under existing #681;
resolve dynamic-secret creation under #1586. Prefer a narrow, generation-bound
broker with provider credentials outside participant control, exact model/tool
allowlists, spend/concurrency/deadline bounds, revocation and body-free audit.
If a workload must receive a credential, prove its effective permissions and
revocation limits. Keep operator MCP and CI identities outside ranges. Detailed
vulnerability reproduction belongs in the repository's private reporting path.

## F08 — Browser protection and user-journey verification remain unfinished (P1)

`config/_browser_security.py` defaults to report-only CSP. It has a useful strict
candidate, referrer/permissions headers and a collector, but report-only does not
prevent execution. `frontend/vite.config.ts` measures coverage without thresholds.
There is one Playwright file: canonical pack list/detail inspection, with an
authenticated session assumed by its comments. The reviewed quality workflow
runs Vitest, lint, types and builds; it does not run that Playwright journey.
jsdom axe prints canvas/color-contrast execution errors during otherwise passing
tests.

**Remedy:** finish #1526 and its #713 accessibility policy dependency; enforce
CSP on the real login, terminal, participant, organizer and administration paths
without broad exceptions. Test authority revocation, token/session failures,
launch-to-terminal, bad readiness, cancelled cleanup and event workflows against
a built application. Reuse canonical OpenAPI clients and the existing renderer.

## F09 — Security gate state must be reconciled to the exact release (P1)

On the reviewed commit, GitHub reports the Sonar analysis gate failed with eight
new issues and an E new-code security rating, despite the scanner job succeeding.
At collection time GitHub reports 95 open dependency alerts (50 high, 35 medium,
10 low) and eight open code-scanning alerts. Those repository-wide counts can
reflect the default branch, duplicate affected manifests and non-runtime tools;
they do not prove 103 exploitable vulnerabilities in this checkout.

**Remedy:** triage the selected commit and built artifacts, refresh existing
#1752/#1756/#1768 where applicable, patch reachable dependencies and unsafe code,
and record narrow, reviewed false-positive/unaffected decisions. Verify runtime
SBOMs and guest images too. A green scan invocation is not a green quality gate.
Fix the reporting contact in #1531: root `SECURITY.md` still directs reports to
the prior organization's PSIRT rather than clearly identifying this product's
maintainer-controlled route.

Historical security issues need explicit reconciliation: participant account
creation now generates passwords by default, although an explicit event-shared
override remains a release-policy choice (#1645). Image promotion now checks
protected validation-run/artifact bindings; the older label-only and unrestricted
dispatch descriptions in #1621/#1646 are not a complete account of current code.
The workflows still share `GCP_SERVICE_ACCOUNT`, so #1699's per-purpose identity
work remains relevant. Bootstrap already defaults to `operator-adc` (#1738).
Preserve these improvements and require effective cloud-permission evidence.

## F10 — The first operator still inherits too much historical knowledge (P1)

The installation loader, typed backend bundle, doctor, preflight, Helm package,
Terraform inventory and build provenance are useful. The remaining fresh-project
GCP rehearsal (#615), scenario readiness repairs (#1910), live verification
backlog (#1361) and AWS-focused disaster-recovery runbook do not jointly prove
that another operator can install and recover the selected GCP release.

Root README instructs users to install retired CyberScript and links to the
removed documentation app and a removed scenario template. There are 332 tracked
architecture files, 291 named preflights. A contributor should not need those
records to reconstruct the runtime. Pending #1531 is the right handbook owner.

**Remedy:** a released GCP deployment bill of materials; fresh install without
maintainer-only resources; upgrade/rollback and database, object, secret and
key-reference restore; measured RPO/RTO; reconciliation before mutating restored
ranges; supported limits and incident runbooks. Reuse #615/#202/#1531 and add the
missing GCP recovery qualification. Third-party IdP/model/storage unavailability
must produce truthful bounded failure rather than invented offline success.

## F11 — Keep the modular monolith; reduce actual maintenance surfaces (P2)

The tracked source inventory includes 518 platform test files and approximately
105k test lines. The platform domain source is split into service packages and
passes eight import-linter contracts. File size alone would misidentify the
13k-line generated API declaration, migrations and vendored JavaScript as design
failures. The more useful targets are 2,102 lines in GCP bootstrap, 1,628 in MCP
policy, large repeated Terraform environment roots and stale production surfaces.

The provisioner still contains scenario-named bootstrap code, retired-path
documentation, and legacy raw SQL outside the cut-over RAES operation family.
Scenario-owned scripts, MCPs, proof validators and walkthroughs must have a
released pack/plugin home; backend-owned identity, safe transfer, execution,
network, readiness and evidence remain reusable backend concerns. Extracting a
script without its release owner and acceptance tests just moves the dependency.

**Remedy:** usage/call-path inventory, owner and replacement per surface; remove
only after supported-path and migration proof. Reuse #1772 for vendor-specific
removal decisions, #1830 for ECS retirement after EKS qualification, and #1937
for the plan reader. Keep GDC available only for its stated operator/cleanup
scope unless a separately reviewed retirement migrates old state. Do not import
upstream scenario internals or create a generic plugin bus for ordinary policy.

## F12 — Backlog state and verification commands overstate completeness (P2)

The baseline has 282 open issues, 71 under a closed CTF GA milestone, three open
PRs and only 39 native dependency edges. Some open issues have merged code but
remaining live or reporting obligations; others retain obsolete architecture or
overlap later work. In particular #1539 has been narrowed to a historical REV1
gate; closing it would not establish this broader adoption standard.

`make test` omits the SPA, MCP package tests and some support-package lanes even
though its description says every no-service lane. Those surfaces have separate
CI jobs; this is a local completeness/documentation gap, not proof that CI omits
them. The ADR registry has 278 rules, of which 233 have no named `checks` entry;
other validators and behavioral tests can still enforce them. All 41 exceptions
need ownership and expiry discipline, not blanket deletion or automatic renewal.
Issue #1698 itself still describes some pre-migration paths and rule numbering.

**Remedy:** [the complete disposition table](backlog.md); one owner per outcome,
native dependencies, evidence-based closure, and a truthful developer command
matrix. Keep required tests/security owners under #1698, update historical
program parents, and resist new milestone-wide feature commitments.

## Source navigation

These links pin representative execution and trust boundaries to the reviewed
commit. Findings also use related tests, provider configuration and issue history;
absence claims reflect repository-wide searches, not just these entrypoints.

| Finding | Source at the baseline |
| --- | --- |
| F01/F02 | [shifter/shifter_platform/shared/raes/manifest.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/shared/raes/manifest.py#L121) |
| F01 | [shifter/shifter_platform/shared/raes/composition_envelope.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/shared/raes/composition_envelope.py#L49) |
| F01/F05 | [shifter/engine/provisioner/raes_plan.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/engine/provisioner/raes_plan.py#L108) |
| F02 | [shifter/shifter_platform/cms/services/_raes_range_create.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/cms/services/_raes_range_create.py#L58) |
| F02 | [shifter/shifter_platform/shared/raes/operation_input.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/shared/raes/operation_input.py#L115) |
| F03 | [shifter/shifter_platform/ctf/models/event.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/ctf/models/event.py#L50) |
| F03 | [docs/adr/index.yaml](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/docs/adr/index.yaml#L2539) |
| F04 | [shifter/shifter_platform/mission_control/api/ranges.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/mission_control/api/ranges.py#L158) |
| F04 | [shifter/shifter_platform/engine/services/_raes_range.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/engine/services/_raes_range.py#L71) |
| F04/F05 | [shifter/shifter_platform/engine/launch_intents.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/engine/launch_intents.py#L43) |
| F04/F05 | [shifter/shifter_platform/shared/operation_envelope.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/shared/operation_envelope.py#L1) |
| F05 | [shifter/engine/provisioner/raes_range_ops.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/engine/provisioner/raes_range_ops.py#L326) |
| F06 | [docs/ops/range-escape-validation.md](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/docs/ops/range-escape-validation.md#L1) |
| F06 | [shifter/engine/provisioner/gcp_range_cell_escape_checks.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/engine/provisioner/gcp_range_cell_escape_checks.py#L39) |
| F07 | [shifter/engine/provisioner/gcp_range_vertex_creds.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/engine/provisioner/gcp_range_vertex_creds.py#L59) |
| F07 | [shifter/packer/scripts/common/claude-autostart-install.sh](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/packer/scripts/common/claude-autostart-install.sh#L33) |
| F07/F11 | [mcp/ops/policy.js](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/mcp/ops/policy.js#L1) |
| F08 | [shifter/shifter_platform/config/_browser_security.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/config/_browser_security.py#L12) |
| F08 | [shifter/shifter_platform/frontend/vite.config.ts](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/frontend/vite.config.ts#L46) |
| F08/F09 | [shifter/shifter_platform/ctf/services/participant/accounts.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/ctf/services/participant/accounts.py#L54) |
| F09 | [shifter/shifter_platform/cms/services/_uploads.py](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/shifter/shifter_platform/cms/services/_uploads.py#L22) |
| F09 | [.github/workflows/packer-gcp-promote.yml](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/.github/workflows/packer-gcp-promote.yml#L136) |
| F09 | [.github/workflows/_gcp-dev.yml](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/.github/workflows/_gcp-dev.yml#L151) |
| F10 | [README.md](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/README.md#L10) |
| F10 | [SECURITY.md](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/SECURITY.md#L1) |
| F10 | [docs/ops/disaster-recovery.md](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/docs/ops/disaster-recovery.md#L1) |
| F11/F12 | [scripts/check_layer_imports/layer_imports.yaml](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/scripts/check_layer_imports/layer_imports.yaml#L1) |
| F12 | [Makefile](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/Makefile#L55) |
| F08/F12 | [.github/workflows/_quality.yml](https://github.com/Brad-Edwards/shifter/blob/b9b82681818fed7da26fcedaa93d37586bc14c74/.github/workflows/_quality.yml#L645) |
