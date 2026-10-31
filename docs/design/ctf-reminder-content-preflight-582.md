# Reminder content and delivery preflight (#582)

Status: architecture guidance, 2026-10-31; no implementation or delivery claim.
The supplied issue and its two acceptance criteria are the contract. This is a
requirement-free run; the historical CTF-1005 label does not authorize requirement
status or traceability changes. This note specializes ADR-051 and ADR-071; it is
not an implementation plan.

## Findings and design boundary

The checkout already implements #1525's transport: `EmailAdapter`, the durable
`DeliveryAttempt` worker, and `shared.email.attempt_email()`. The trusted
`templates/ctf/email/notice/reminder.{html,txt}` pair already includes the event
name and start, and its base pair includes the access URL. Deployment availability
is still conditional on the configured backend. The missing behavior is recipient
time-zone selection: `_recipient_facts()` currently uses `CTFEvent.event_timezone`.
Neither `CTFParticipant` nor `management.UserProfile` stores a recipient zone.
Browser-local dates on `EventHomePage` do not supply a background worker with one.

Keep the existing ownership chain:
`event.scheduling` / `run_ctf_scheduler` → `notification.send_reminder` →
`notification.ledger.stage_notice` → campaign/release/materialization → delivery
worker → email adapter → `shared.email`. One occurrence targets the existing
event-qualified audience; do not create a campaign per recipient to personalize
its body. `send_reminder` stages content; it must not perform transport I/O.

The smallest recipient-preference extension fits the existing event-scoped
`CTFParticipant` profile, `participant.profile.update_own_profile()`,
`ParticipantProfileView`, its serializers/projection, and `AccountPage`. Use one
optional IANA zone value there, not a new preferences table or parallel field on
both participant and platform profile. This is a recommendation for this CTF
scope, not a platform-wide preference design. Preserve owner-only mutation and
existing participant permissions; a delivery email is never an identity key.
The preference needs an actual supported input surface, not just a migration or
a test-only attribute. A browser zone can suggest a value; locale, IP location,
server `TZ`, event zone and fixed UTC offsets cannot establish recipient choice.

Validate supplied zones with the standard `zoneinfo.ZoneInfo` machinery, bounded
to the owning field's length, through one reusable domain validator called by
supported writes. Reuse Django/DRF validation and CTF errors; do not maintain a
second zone database, regex-only validator or exception hierarchy. Unknown and
malformed supplied values are invalid, not silently accepted. Legacy/missing
preferences need a documented fallback: valid event zone, then UTC, with the
actual zone displayed. That fallback is not evidence of knowing the recipient's
zone, especially before first login. Acceptance evidence must distinguish the
known-preference case from that unavoidable unknown-preference limitation.

The existing profile request serializer is a plain DRF `Serializer`; it does
not itself reject unknown keys. Where closing this extended write contract is
needed, reuse `shared.api.closed_serializer.ClosedSerializer`, not a bespoke
key checker. Field validators do not run automatically on every Django `save()`;
direct service writes must enforce the same zone contract. Keep omitted versus
explicitly cleared preference semantics distinct, as the profile service does
for its existing optional fields.

Recipient-local presentation belongs in the adapter's scalar context. Preserve
the documented event-zone meaning of `event_start`, `event_end`, and
`event_timezone` from [#580](ctf-ledger-email-template-boundaries-580.md); add
explicit recipient-display scalars for reminders instead of silently changing
every notice kind. Parameterize the existing time formatter by the resolved zone.
Convert the aware event instant at its event date (including DST), and display
the date, time, IANA zone and preferably numeric offset. Scheduling remains UTC.
Retries retain the same revision, recipient coordinate and delivery key; live
event facts and the live display preference may be rendered again, consistently
with #580. Do not mutate immutable snapshots or revisions to refresh presentation.

## Cross-cutting gates and incumbents

Paths below are relative to `shifter/shifter_platform/` unless stated otherwise.

| Layer | Incumbent and obligation |
| --- | --- |
| Input, authentication and policy | Extend `ctf/api/participant_views.py`, `serializers/participant.py`, `projections.py` and `services/participant/profile.py`. Keep `CTF_PARTICIPANT_PERMISSIONS`, `ctf_actor_user`, active-event resolution, token read/write scopes and the locked own-participant check. Do not accept a client-selected participant, tenant, email coordinate or system flag. Keep `ctf/api/operation_policy.py` and `tests/contracts/identity_authorization_surfaces.json` accurate; regenerate OpenAPI and frontend types through the existing API-contract workflow if the profile contract changes. |
| Campaign shapes and content | `ctf/communication_contracts.py` owns target, audience, trigger, channel, acknowledgement and content validation. `CampaignDraft`, `NoticeKind`, model validation and database constraints already define the schema. Continue through `create_system_event_campaign()`; never bypass validation for a reminder. |
| Scope and recipient authority | `communication/admission.py` and `workspaces.services` own exact resource placement and admission. `AdmissionActor(system=True)` is reserved for the trusted lifecycle caller, not a public shortcut. `audience.recipient_queryset()` uses `viewing_participant_q()`, not a new registered-user loop or an email lookup. The viewing predicate includes disqualified participants and excludes banned/unregistered ones; hidden/observer status is not an email opt-out. `recipient_fence.py` rechecks eligibility before delivery. |
| Persistence and workflow | `release.py` and `materialize.py` atomically create pinned revisions/intents, snapshots, per-channel commands and release evidence. Keep deterministic occurrence identity, uniqueness, locking, admission/backpressure, cancellation and retention. `delivery.py`, `worker_config.py`, `disposition.py` and `lifecycle.py` own leases, retry ceilings, current fences and persisted delivery outcomes. No database lock spans email I/O. |
| Template and URL security | Use trusted `notice/reminder` and `_base` pairs through `shared.email.render_template()`. Pass scalar strings, retain HTML autoescape and plain-text output, and never use organizer text as template source or mark it safe. Build links through `shared.site_url.validated_site_url()` and Django `reverse()`, never request Host, event-authored origins or credentials. Preserve subject newline handling and Django MIME/header validation. |
| Secret and privacy boundary | `RecipientSnapshot.delivery_coordinate` remains encrypted; only `EmailAdapter._read_coordinate()` decrypts its bound snapshot. Never retarget a retry from the live participant/account email or expose coordinates through preference projections. Missing coordinates are terminal; unreadable encrypted coordinates are bounded retriable dependency failures. No password, bootstrap token, signed login secret or guest access credential belongs in a reminder. |
| Error envelopes and evidence | Reuse `CTFValidationError`/`CTFCommunicationError`, `shared.api.errors.api_error_response`, `EmailOutcome` and `DeliveryOutcome`. Parser/provider text and rejected values must not enter responses or exception causes. The worker isolates adapter exceptions with a class-only log. Reuse `communication/audit.py`, `shared.audit`, `metrics.py`, `reporting.py` and persisted disposition; log IDs/closed outcomes, not names, addresses, bodies, zone input or secrets. A metric failure must never trigger a resend. |
| Configuration and environment shapes | `config/_email.py` selects the backend and availability; `_ctf_communication_settings.py` and `WorkerConfig` validate bounded policy and timeout/lease relationships. `config/_runtime_env.py`, `_product_env.py`, `_env_manifest.py` and generated `env-manifest.json` own runtime bindings. No reminder-specific env setting or secret is needed. If implementation discovers a real runtime gap, use root `shifter/installation/registry.py`, its typed contracts/settings and `runtime_inventory{,_aws,_gcp}.py`, plus the existing runtime renderers; do not inject an ad hoc worker variable. |
| Host, container and transport | The root `platform/charts/shifter/templates/ctf-communication-worker-deployment.yaml` uses the portal image/entrypoint, shared runtime ConfigMap, worker identity, database role and heartbeat. `entrypoint.sh` hydrates field-encryption and email secrets; `scripts/gcp/render_runtime_env.py` at repo root emits secret references, not values. Preserve existing secret-reference shapes, worker IAM, network egress and pod security. No secret in argv, shell tracing, environment dumps, Helm values or Terraform state. Use the image's available IANA data; prove the selected zones resolve in the worker runtime. No OS time-zone mutation, new subprocess, provider SDK, external time-zone service or new hosting. |
| Repository boundaries | Root `.ground-control.yaml`, `.gc/plan-rules.md`, `docs/adr/index.yaml`, `.importlinter` and `scripts/check_layer_imports/layer_imports.yaml` remain authoritative. Keep domain imports behind public service boundaries; `shared.email` must not learn CTF personalization. ADR-065's retired senders and maintenance fence remain intact. |

## Gotchas requiring explicit treatment

- **Rendered mail versus retained body:** the campaign body is deliberately
  generic. Both delivered MIME parts must contain the required facts. Putting
  arbitrary event names into the validated Markdown body can reject tag-like
  text; putting an absolute `SITE_URL` there invokes the separate external-host
  allowlist (empty by default). Use the escaped trusted wrapper for event facts
  and the validated portal link. Do not weaken the content validator or add the
  portal host to policy merely to make a reminder pass. Email-only reminders do
  not create an in-app receipt; inbox personalization is a separate contract.
- **Access link semantics:** `reverse("ctf:participant_event")` is `/ctf/event/`.
  `ParticipantProfileView` and participant APIs resolve the active event. An email
  for another participation cannot assume this opens its qualifying event. Verify
  login/required-password-change return navigation and multi-event behavior. If
  event selection is needed, use authenticated, authorized event selection;
  appending an unconsumed event UUID or silently changing session state on GET
  does not solve it. The access URL is a portal link, not a range endpoint or a
  credential. Event-selection redesign is not silently included in #582.
- **Disabled intervals:** `event.scheduling._schedule_event_tasks()` currently
  uses `event.reminder_hours or [24, 1]`, so `[]` schedules defaults despite the
  model's “empty disables” contract. Its `isinstance(h, int)` also admits booleans,
  and duplicate intervals create duplicate tasks. Reconcile these at the existing
  event configuration/scheduling boundary if touched; no second scheduler/parser.
- **Occurrence and lateness:** the current identity includes event start and
  `hours_before`. Preserve distinct lead-time reminders and deduplicate retries.
  The producer reads event start before `stage_notice()` takes its lock: review
  concurrent reschedule/claim behavior rather than assuming that read is fenced.
  `SEND_REMINDER` stages a manual release; the absolute-time communication grace
  policy does not automatically govern it. Backlog or transport retries can cross
  event start. Explicitly resolve stale “starts soon” behavior within existing
  lifecycle/worker owners; do not invent a reminder retry queue or claim a
  start-time expiry that the current code does not implement.
- **Unavailable email:** `stage_notice()` retains a campaign but creates no
  runnable intent when email is unavailable. `_handle_send_reminder()` logs the
  result and returns; it does not arrange future release when configuration
  changes. Availability at execution and automatic catch-up are different
  promises. Do not replay historical campaigns indiscriminately on activation.
- **Delivery truth:** staging `accepted` is ledger acceptance. A completed task
  is not email success; zero recipients and email-less participants are not
  successful sends. Provider acceptance is not mailbox delivery. Preserve the
  documented duplicate window after provider acceptance but before settlement;
  a stable Message-ID does not make delivery happen only one time.

## Review evidence and limits

Build on `tests/ctf/test_communication_email_adapter.py` (already exercises the
reminder through staging and worker), `test_services/test_notification.py`
(occurrence reuse/unavailable mail), `test_me_profile_api.py`, scheduler handler
and concurrency tests, and communication delivery/lifecycle/settings/PostgreSQL
restart tests. The existing reminder assertion proves **event-zone**, not
recipient-zone, rendering. Required evidence includes two recipients with
different zones for one instant, DST/date rollover and offsets, missing/invalid
zones, profile ownership and API errors, both MIME parts, hostile event names,
portal navigation, unavailable/missing-email outcomes, and retry stability after
event/preference changes. Exercise actual services with synthetic fixtures and
local mail backends, not real email or new external services.

The extension seam is recipient display-zone selection feeding the existing
zone-parameterized formatter and scalar template context. Future notice kinds
can opt into it without changing scheduling, provider transport or the meaning
of event-zone fields. Time zone is neither locale nor identity. No generic
personalization engine, translation/theming system, editable templates, calendar
attachments, organizer-recipient expansion, new outbox, credential delivery,
requirement edits or infrastructure rollout is part of this preflight.

Documentation changes require the full ADR guard. Subsequent Python changes
require the repository's Ruff/import checks and relevant service tests; API/UI
changes also require contract/frontend checks. Run chart/Terraform/workflow
checks only when those artifacts change. Passing structural checks does not
prove recipient preference capture, live deployment availability or delivery.
