# Shifter CTF

Capture-the-flag is a Django app (`ctf`) layered on the platform's range system. It
owns event/challenge/scoring data and orchestrates a dedicated range per participant
by calling the existing CMS range services; it does not introduce a second
provisioning path.

## Responsibility

- Model CTF events, challenges, flags, teams, brackets, submissions, hints, and
  notifications.
- Validate flag submissions and compute scores and leaderboards.
- Provision and clean up one range per participant from the event's scenario.
- Serve participant, organizer/admin, and JSON API surfaces.

## URL Surface

`ctf.urls` (`app_name = "ctf"`) mounts three groups under `/ctf/`:

| Group | Prefix | Audience |
|-------|--------|----------|
| Participant | `/ctf/` | Competitors: dedicated login, password change, dashboard, challenges, range, scoreboard, team |
| Organizer/Admin | `/ctf/admin/` | Organizers: events, challenges, participants, teams, brackets, ranges, notifications, analytics |
| API | `/ctf/api/` | JSON endpoints for events, challenges, scenarios |

Participant access uses isolated temporary username/password accounts; organizer views are gated to CTF
organizer roles. The portal sidebar distinguishes participant-only users so the two
surfaces stay structurally separate (see ADR-013).

## Models

Models live in the `ctf.models` package (split from a single module in PR #856);
`__init__` re-exports every public symbol so `from ctf.models import X` is stable. All
inherit `CTFBaseModel` with a `SoftDeleteManager` (soft delete by default).

| Model | Purpose |
|-------|---------|
| `CTFEvent` | Competition: window, scenario, capacity, team mode, throttles, scoreboard policy, cleanup policy |
| `CTFChallenge` | Scored task: category, points, difficulty, hashed flag, release time, prerequisite, target instance/port, tags/topics |
| `CTFFlag` | One or more flags per challenge; stored as a hash with type, case sensitivity, and validator config |
| `CTFTopic`, `CTFChallengeTag`, `CTFChallengeFile`, `CTFChallengePrerequisite` | Challenge taxonomy, attachments, and unlock graph |
| `CTFBracket`, `CTFTeam`, `CTFParticipant` | Cohorts, teams, and per-user participation |
| `CTFSubmission`, `CTFAward` | Flag attempts (correctness, points, attempt number, source IP) and manual point awards |
| `CTFChallengeRating` | Participant difficulty ratings |
| `CTFHint`, `CTFHintUsage` | Optional, point-reducing hints and usage tracking |
| `CTFNotification`, `CTFEmailTemplate`, `CTFScheduledTask` | Announcements, reminder templates, and scheduled work |
| `CTFContentHydrationReceipt` | Digest, object-identity fingerprints, bounded counts, and pristine/drifted state for scenario-managed event content |

Flags are persisted only as hashes (`flag_hash`); plaintext is never stored after
challenge creation, and submission checking compares against the hash.

## Services

Business logic lives under `ctf.services` (views stay thin):

- `event`, `challenge`, `flag`, `bracket`, `hint`, `award`, `attachment`,
  `email_template`, `notification`: entity operations.
- `participant/`: `lifecycle`, `bulk_import`, `queries`.
- `scoring/`: materialized-leaderboard hot path with an authoritative recompute
  fallback (`get_scoreboard`, `calculate_score`, ranks, stats, timeline, and the
  `recompute_*` maintenance helpers).
- `authorization`, `audit`: access checks and audit trail.

### Scenario content hydration

`ctf.services.content_resolution` resolves a deployment-owned scenario
reference through the provider-neutral `shared.cloud.ObjectStorage` protocol.
It performs a bounded head/download with an ETag, generation, or version
precondition, verifies the declared SHA-256 digest before parsing, and removes
temporary bytes in `finally`. Provider errors, object coordinates, validator
configuration, and bundle bodies do not cross the public error boundary.

`ctf.content_bundle` is a closed, data-only
`shifter-ctf-content/v1` contract. It validates resource bounds, challenge
identities and orders, native flag policy, prerequisite references, and the
complete DAG before a database transaction begins. It supports native static,
regex, and HTTPS HTTP validators; it does not execute package code or enable
programmable validators.

Event creation composes the resolver with
`ctf.services.hydrate_event_ctf_content`. The service locks the event, checks
ownership and state, writes the graph through native challenge/hint/prerequisite
services, creates one receipt, and records a strict audit event in one atomic
transaction. An exact pristine replay is a no-op. Foreign rows, changed source
evidence, shape mismatch, or a drifted receipt fail closed.

All native organizer mutation paths mark an existing receipt drifted inside
their transaction. Both manual and scheduled activation require a pristine
receipt whose scenario and digest match current deployment configuration.
Scenarios without a configured content reference retain existing behavior.

The deployment reference catalog is the separate closed contract
`shifter-ctf-content-references/v1`, parsed at startup from private deployment
configuration. This feature does not reuse the RAES package catalog or persist
bundle bodies in scenario/event JSON. See the
[operator runbook](../../dev/ctf-scenario-content) for publication, provider
IAM, runtime settings, rotation, and failure recovery.

### Range Provisioning

`ctf.services.range` provisions a range per participant from `event.scenario_id` via
CMS range services, the same path Mission Control uses to launch a range.

- `provision.py`: `provision_participant_range(participant_id)` runs under a
  per-participant row lock so concurrent manual and scheduled provisioning cannot
  double-assign a range, with an exponential-backoff retry wrapper.
- `batch.py`: throttled event-wide provisioning, pacing participant spin-ups.
- `lifecycle.py`, `status.py`, `tasks.py`: teardown, status reads, and task wiring.
- `vpn.py`: resolves the current participant's generation-bound OpenVPN profile
  through the CTF → CMS → Engine service boundary. The participant API exposes it
  only through `POST /api/v1/ctf/range/vpn-profile/` with the exact
  `ctf:vpn-profile:read` token scope, CSRF enforcement for sessions, a credential
  delivery rate limit, no-store response headers, and a delivery audit event.
- `recovery.py`: `recover_participant_range(participant_id, *, strategy,
  operator, spare_range_instance_id=None)` recovers a destroyed participant range
  (organizer-only). `rebuild` provisions a fresh range via the CMS bridge; `reassign_spare`
  consumes an available `CTFSpareRange` from the participant's own event pool and
  reassigns its ownership to the participant (terminal access is keyed on the range's
  owning user, so both the CMS and engine ownership move together via
  `cms.services.reassign_range_owner`). The intent is persisted as a `CTFRangeRecovery`
  row keyed on participant + old range + strategy; resumption after a partial failure is
  data-driven (recorded replacement id and the live old-range status), so retries never
  duplicate the replacement or the audit row. The old range is always destroyed; there
  is no disposition/forensics-retention choice. Recovery writes one shared audit
  row.
- `spares.py`: `provision_event_spares(event_id, target_count, *, operator=None)` tops up
  an event's prewarmed spare-range pool (`CTFEvent.spare_range_count`), each spare owned by
  a dedicated, auto-created managed system user (never a `CTFParticipant`) until consumed.
  `get_event_spare_summary(event_id)` reports pool counts for the admin surface;
  `cleanup_event_spares(event_id)` tears down unconsumed spares and their managed users at
  event teardown. A spare's status reaches `ready`/`failed` via the existing
  `cms.services.range_status_changed` projection (`ctf.signals.sync_ctf_spare_range_status`).

Long waits are broken into heartbeat-touching chunks so the scheduler's liveness file
does not go stale and provisioning stays responsive to shutdown.

### Participant OpenVPN access

The CTF bridge mints a closed OpenVPN capability from the event cleanup deadline
and the single participant-access Kali member, falling back to the unique
hydrated Kali member for legacy templates without access declarations. Engine persists it beside the
range; Mission Control ranges and topology without that capability do not create
a VPN edge. Provisioning may attach a request-owned OpenVPN gateway only when
that capability is present. The provider adapter enforces a `/32` path to that member and
stores the server identity and participant profile in the provider secret store.
Django persists only a closed, non-secret binding containing the generation, owner,
target, endpoint, profile version, and secret reference. Profile bytes are resolved
and validated in memory only after CTF provenance, participant ownership, range
status, request generation, and target membership checks pass.

The participant range-status projection exposes only
`vpn_profile_available`; it does not expose provider, endpoint, target, or secret
metadata. Paused and non-ready ranges cannot deliver a profile. Destroy removes the
provider material. Ownership transfer is rejected while a participant VPN binding
exists because clearing a database reference cannot revoke a downloaded credential;
recovery must destroy that generation and provision a new one for the replacement
owner before it becomes ready.
Certificates remain valid through the trusted teardown deadline, subject to a
397-day maximum capability window that fails before cloud mutation. On GCE, each
generation gets a distinct no-role gateway service account with read access only
to its own server secret; the shared range-host identity cannot read VPN secrets.
See [the provider-neutral range substrate](../../architecture/provider-neutral-range-substrate)
for the ADR-039 contract and [the issue 1695 architecture preflight](../../architecture/ctf-openvpn-participant-access-preflight-1695)
for the threat model and containment decisions.

## Scheduled Work

Two management commands operate the event runtime:

- `run_ctf_scheduler`: long-running scheduler that drives batch range provisioning,
  cleanup, reminders, and scheduled tasks, writing a liveness heartbeat.
- `ctf_recompute_leaderboard`: recomputes materialized leaderboard columns
  authoritatively when reconciliation is needed.

## Boundaries

- CTF reaches the range system through CMS/engine **service calls**, consistent with
  ADR-001 (cross-layer access goes through service boundaries). `ctf` is one of the
  recognized layers in the import policy.
- Per-function complexity stays under the Ruff C901 gate (ADR-012); existing
  high-complexity CTF functions carry tracked exemptions.

## See Also

- [CTF](../../features/ctf): participant guide.
- [CTF Organizer Guide](../../features/ctf-organizer-guide): running an event.
- [Native CTF scenario content](../../dev/ctf-scenario-content): private
  publication and deployment binding.
