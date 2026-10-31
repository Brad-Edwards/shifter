# CTF-706 cancellation notice architecture preflight

Assessment: 2026-10-31, issue #662, current checkout. This is design guidance,
not an implementation plan or evidence of delivery. The supplied requirement
is sufficient; `.ground-control.yaml` names `OpenRAE/bigrae` as the canonical
repository. Historical repository names in issue payloads are not destinations.

## Finding and boundary

`ctf.services.event._lifecycle.cancel_event` calls the retired
`notification.send_announcement`; the API test replaces that first-party
function and conceals the failure. Replacing the call with `stage_notice` alone
does **not** fix it: `release._assert_release_allowed` rejects cancelled events,
`delivery._fence_reason` suppresses their messages, and `on_event_cancelled`
fences their scheduled intents and unclaimed attempts. `NoticeKind` also has no
cancellation presentation. The generic notice helper's owner-authority docstring
is stale: its implementation uses explicit system admission, not owner policy.

Use the existing communication ledger for one server-authored, event-qualified
cancellation occurrence. The only exception to the cancelled-event fence is
that occurrence's participant notice. Ordinary announcements, organizer alerts,
scenario work and other system notices remain fenced. Campaign/intent cancellation,
participant removal, erased coordinates, tenancy and retention still apply.
Do not treat `origin`, a template kind, subject text, a missing actor, or an HTTP
flag as authority. The exception needs trusted lifecycle creation, exact event
binding, an immutable closed notice identity and proof that cancellation occurred.
Apply the same classification at declaration, release, lifecycle fencing and
worker pre-I/O checks; do not scatter independent lists of exemptions.

Reuse `notification.ledger`, `notification.delivery_milestones`,
`create_system_event_campaign`, `CampaignDraft`, and the existing release/delivery
owners. Extend their closed lifecycle-notice seam only as needed. `NoticeKind`
selects presentation; keep its role distinct from admission. A new kind must
agree with model choices, `ctf_comm_campaign_notice_kind_valid`, migrations and
the trusted HTML/text template pair. Public authoring remains announcement-only;
public create/revise/schedule paths must not forge or repurpose the exception.

## Durability and truthful outcomes

Cancellation, ordinary-work fencing and a durable notice obligation belong in
the same database commit. A post-commit callback alone, a draft with no recovery
owner, or catching an error and returning success can lose the only notice.
Transport failure must not block teardown. Reuse scheduled declarations,
`RELEASE_COMMUNICATION`, and the delivery worker's retry/disposition machinery;
do not add an outbox, thread, mail loop or second scheduler. The organizer alert
implementation demonstrates transactional declaration and retained retry evidence,
but its organizer audience and task-specific pending-alert payload are not this
participant contract. Interactive cancellation has no claimed lifecycle task on
which to assume that fallback exists.

Existing constraints must be reconciled explicitly: `schedule_declaration`
rejects `event_lifecycle` triggers (they are immediate milestones), while the
organizer pattern uses a pinned `absolute_time` declaration. Do not pass a
lifecycle trigger to the clock scheduler and assume it works. Reuse the existing
declaration representation and pin the cancellation occurrence separately from
mutable retry time. `_cancel_event_tasks` selects all pending event tasks by default;
the new obligation must survive both that operation and repeated event fencing.
Use event-first, then campaign/intent locking and existing uniqueness/replay
checks. No external mail or policy RPC belongs inside these SQL locks.

One stable event/kind/occurrence identity must survive restart and retries;
fresh UUIDs or current timestamps on each attempt are not occurrence identities.
Released snapshots never expand on replay. Audience resolution remains at the
admitted occurrence, with live removal fences thereafter. Backpressure, cache
outages, unavailable email and grace expiry require durable, bounded outcomes;
do not silently drop the obligation, disable caps, or mark unavailable mail sent.
The factory's `authoring_rate_limited=False` is currently documented for organizer
alerts only: any reuse for this bounded system occurrence must update that narrow
contract, not exempt all system traffic. Release caps and retry budgets remain.

Retain the existing participant email channel contract. This issue does not
authorize silently substituting the organizer in-app channel policy. Email-only
campaigns create no inbox receipts. A deployment without delivering email needs
an observable unavailable/pending outcome and an explicit recovery path; merely
returning `channel_unavailable` is not completion. Ledger acceptance, backend
acceptance, receipt/read and verified range destruction are separate facts.
Do not promise participants receive mail before ranges disappear. Provider
acceptance cannot be recalled; a crash before settlement permits at-least-once
duplicates, bounded by existing command identity and stable Message-ID.

## Cross-cutting contracts

Paths below are relative to `shifter/shifter_platform/` unless stated otherwise.

| Layer / canonical incumbent | Constraint on the intended change |
| --- | --- |
| HTTP authentication and input | Preserve `EventLifecycleView`, `CTF_ORGANIZER_PERMISSIONS`, event-write token scope, session/CSRF handling and `EventLifecycleRequestSerializer`. The request selects the lifecycle action only, never recipients, content or system authority. No new endpoint or DTO is needed. If shape changes, reuse `shared.api.closed_serializer` and `shared.api.strict_json`, not a new parser. |
| Native authorization and scope | Preserve `apply_event_lifecycle_transition`, `authorization.admit_event_actions`, `actors.EventActionAdmission`, `CredentialContext` and live `event.manage_lifecycle` admission. Prepare policy before locks and validate against the locked binding. `workspaces.services.resource_scope_from_ids`/`resolve_resource_scope` own account/organization/workspace ancestry, including account-only events. System notice execution is a bounded consequence of an authorized transition, not impersonation of `created_by` or a new communication permission demanded of the cancelling user. |
| Audience and live eligibility | Use `communication.audience.recipient_queryset`/`resolve_recipients`, `participant.queries.viewing_participant_q`, and `recipient_fence.recipient_still_eligible`. Registered viewing participants include disqualified participants and eligible observers/hidden participants; invited-only, banned and removed rows remain excluded. “All registered” does not authorize overriding removal or confidentiality policy. Do not use the ranking predicate, user/email deduplication, workspace membership or organizer recipients. |
| Content and rendering | `communication_contracts` owns target UUIDs, closed audience/trigger/channel shapes, acknowledgement policy, subject limits and safe Markdown/link validation. Untrusted event names can exceed the subject bound or contain forbidden body syntax; use fixed bounded text or the established safe subject fallback, never truncate arbitrary markup or bypass validation. `shared.email.render_template` and escaped scalar context own HTML/text rendering. Keep credentials and operational diagnostics out of content. |
| Persistence and audit | Keep `CTFBaseModel.full_clean`, immutable campaign/revision/intent fields, recipient-kind and uniqueness constraints, soft-delete-aware queries, `materialize_recipients`, `audit_communication_release`/`shared.audit`, and lifecycle mutation audit. No raw SQL/status update to evade model validation, no new repository facade, and no use of audit rows as queue state. |
| Secrets and transport | `RecipientSnapshot.delivery_coordinate` uses `shared.field_encryption`; only `communication.adapters.email` decrypts the captured coordinate. Keep reference-only `DeliveryCommand`, existing channel adapters and `shared.email.attempt_email` with its closed outcomes, receipt validation and whole-attempt deadline. Do not reread a changed address or expose decrypted values in task metadata, logs or exception causes. |
| Configuration and environment shapes | `config._ctf_communication_settings`, `config._product_env`, `WorkerConfig`, `backpressure`, `config._email` and `config._runtime_env` already own bounded settings, timeout < lease, retry/grace limits and backend availability. `shared.site_url.validated_site_url` requires a safe credential-free public origin; the content link-host allowlist is a separate gate. No new environment/config keys are needed. Keep entrypoint hydration's `EMAIL_API_KEY_SECRET_ID` -> secret JSON `api_key` -> runtime `EMAIL_API_KEY` shape and the existing field-key path. |
| Host and deployment | Existing `run_ctf_scheduler` and `drain_ctf_communication_deliveries` processes own execution. Repository `platform/charts/shifter/templates/ctf-communication-worker-deployment.yaml` supplies the established worker args/env and health probes. Reuse its deployment and provider configuration; introduce no subprocess sender, provider, hosting, network destination or credentials in argv. Secret values stay in the existing runtime secret path, never checked-in Helm values or command arguments. |
| Errors and observability | Reuse `CTFCommunicationError` and the existing CTF exception family, `ctf.api._base.ctf_error_response`, and `shared.api.errors.api_exception_handler`. Do not return `str(exc)`, `.details`, provider replies or tracebacks. Use bounded codes and event/campaign/intent IDs; `communication.metrics` and `disposition.refresh_delivery_state` own operational outcome reporting. Strict audit failure is not a harmless mail failure. |

## Existing adjacent risks and non-goals

The issue's statement that everything else works is not a new acceptance proof.
The HTTP lifecycle view wraps the service and audit in an outer transaction;
leaving the service's inner `atomic()` does not mean cancellation has committed.
`cleanup_event_ranges` performs additional `event.manage_ranges` admission and
can dispatch work before that outer commit. A lifecycle-only caller, cleanup
exception or later audit failure can therefore affect cancellation/notice commit
behavior. Preserve explicit authority, test this composition, and do not claim
transactional cloud teardown. Moving an external effect to an unrecorded
`on_commit` callback does not provide recovery. Use existing launch/cleanup
ownership if this boundary needs correction; keep any larger repair separate.

Keep `EventStatus`, `VALID_TRANSITIONS`, `_transition_event`, range cleanup and
submission gates as their existing owners. Cancellation is allowed from draft,
registration, active and paused; historical “scheduled” is not a fifth state.
Keep cancelled history and active-list exclusion; do not delete events, participants
or evidence to stop work. Cleanup dispatch counts are not verified destruction.
The [CTF-701 preflight](ctf-event-lifecycle-preflight-701.md) covers state authority;
this change does not redesign automation, cleanup, history, scoring or registration.
The retired credential resend path is also out of scope: do not revive `_send_email`,
`send_announcement` or HTTP 410 routes, and never put passwords into this ledger.

The extension seam is the existing server-owned lifecycle notice declaration:
kind, event, stable occurrence, validated content and channel policy. Reuse that
seam for the next lifecycle notice; do not create a generic `allow_cancelled` or
`ignore_fences` option. A later channel addition uses the existing channels/adapters,
not another recipient resolver or notification store.

## Evidence required of implementation

Exercise the real cancellation API/service, declaration, release, worker and
template path with a local mail backend or external transport double. ADR-019
prohibits patching the first-party sender, ledger, scheduler or adapter to make
this pass. Reuse `test_communication_lifecycle_wiring.py`, delivery/admission
PostgreSQL tests and `test_communication_email_restart_postgres.py` patterns.
Cover every pre-ended state, terminal refusal, empty and mixed eligible cohorts,
tenant isolation, ordinary/multi-event fencing, forged exception attempts,
participant removal before I/O, unavailable transport, backpressure, replay,
rollback and restart. Verify recipient commands and actual backend acceptance,
not just a call count or event status. PostgreSQL is needed for lock/race claims;
SQLite success is insufficient. Check the existing API audit/cleanup composition.

Repository enforcement remains `.ground-control.yaml`, `.gc/plan-rules.md`,
`docs/adr/index.yaml`/`exceptions.yaml`, `.importlinter`, `.pre-commit-config.yaml`,
`.github/quality-path-filters.yaml` and the root `Makefile`. Run the full ADR CI
guard for these architecture docs; implementation additionally needs the native
import checker, relevant platform tests, migration checks when choices change,
and the repository's completion checks. No new exception or weakened baseline.
