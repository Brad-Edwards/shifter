# Participant milestone email: CTF-801 / #664

> Issue-scoped architecture preflight, 2026-10-31. Guidance only; no runtime
> behavior or requirement status changes. Read the
> [current communication contracts](../technical/shifter_platform/ctf-communications.md)
> and ADR-051, ADR-065 and ADR-071 for implemented behavior.

## Decision and scope

CTF declares the milestone, content and event-qualified audience. The existing
communication ledger owns asynchronous execution; PLAT-103's `shared.email`
owns transport. #1525's email adapter and durable worker are present in this
checkout. The supplied issue's dependency wording and legacy traceability are
historical, not reasons to build another sender. No new abstraction is needed.

#664 supplies registration confirmation through that ledger. Cancellation is
owned by #662 and needs the coordinated fence specialization below. Existing
readiness, failure and results producers remain integration targets, with the
limitations recorded here; their existence does not prove the entire requirement.
This note is design guidance, not an implementation sequence.

Application paths below are relative to `shifter/shifter_platform/`.
`communication/` means `ctf/services/communication/`.

## Milestone boundaries and gotchas

| Milestone | Owning boundary and required meaning |
| --- | --- |
| Registration | `participant.accounts.provision_participant_seat` is the common seat-creation seam for `participant.lifecycle.add_participant`, CSV `bulk_import_participants`, and `create_participant_accounts`. Public approval calls `add_participant`; API imports also call it. Declare once for a newly registered participation, using its immutable participant UUID, inside the caller's admission transaction. Approval and add must not both declare. Duplicate/rejected intake, failed admission and rolled-back seats create no deliverable notice. |
| Cancellation | `event._lifecycle.cancel_event` commits cancellation and communication fencing, then calls the retired `send_announcement`. Replacing that call with ordinary `stage_notice` still fails: release rejects cancelled targets and worker preflight suppresses their commands. #662 owns the narrow exception described below; keep range teardown independent of email. |
| Range ready | `range.status.get_range_status` calls `_notify_range_ready` only when a read changes the cached status to ready. `signals.sync_ctf_participant_range_status` can update the same cache first, producing no notice; no read at all also produces no notice. A reliable milestone must be declared from the existing durable CMS status-delivery boundary, not browser polling. Reuse `cms.services`/`ctf.bridges`, the canonical resource status and current assignment; add no competing range subscription or cross-layer model import. |
| Provisioning failure | `range.batch._notify_provision_failures` declares notices for participant IDs in batch errors. This does not establish coverage of later asynchronous CMS failures. Any added producer must use the same trusted lifecycle boundary and participant/generation identity, with fixed participant-safe text, never provider diagnostics. |
| Final results | `event._lifecycle.finish_ended_event` recomputes the leaderboard before `send_event_results`; automatic endings also converge through event automation. Preserve that order and the single scoring owner. Current email announces that results are available and links to the event; it contains no final rankings. Do not claim results-in-email coverage from that test alone. If rankings are required, use the authorized scoring projection and its hidden/observer/disqualification rules, never a second score calculation. |

Anonymous `submit_public_registration_request` remains intake only: no email,
user, invitation, membership or range effect. An unverified delivery address is
contact metadata, never identity or permission. A confirmation is not credential
issuance or login success. Email-less generated seats remain valid: record an
honest unavailable-coordinate outcome, never invent an address or send to an
organizer/platform account instead.

The existing email adapter links every participant kind to `ctf:participant_event`.
`live_participant_for_user` admits password login only during active/paused event
time. Registration, cancellation and post-event results therefore cannot assume
that a recipient can log in immediately. Put essential confirmation/cancellation
facts in the message; make schedule/timezone and access timing explicit. Verify
results-link usability through the existing read-access contract; do not weaken
authentication, publish private event pages, or add signed login tokens to make
a link work. A reachable results projection is a separate access concern if the
current contract cannot provide it.

## Durable handoff and failure isolation

Reuse `notification.ledger`, `CampaignDraft` and
`create_system_event_campaign`. The durable handoff must commit with the actual
registration, including public approval's outer transaction. The incumbent
pattern for separating declarations from fan-out is
`communication.scheduling.schedule_declaration`: pinned campaign/revision,
scheduled intent and `RELEASE_COMMUNICATION` task, with only the intent ID in task
metadata. `notification._organizer` demonstrates this transaction pattern;
organizer recipients and its authoring-rate exemption are not participant policy.
Use the same release service, backpressure, lateness and task-recovery owners.

Do not call `stage_notice` unexamined from a seat loop: it currently releases
immediately, including audience materialization, admission budgets and strict
audit. Transport is asynchronous, but that work and its failures remain on the
registration path. Bound declaration work for bulk admission; defer release-time
backpressure to the existing scheduler. Do not silently lift system authoring
limits or copy the organizer-only exemption. Any admission-policy specialization
must remain bounded by genuine, unique seat creation and retain shared budgets.

No provider call, provider wait, retry loop or process-local thread belongs in
the triggering action. Delivery failure settles the command and never reverses
registration, cancellation, provisioning or event completion. Storage/strict
audit failure during the primary transaction is not failed email delivery and
must not be disguised as a successful durable handoff. A notification error caught
inside an atomic block needs its own savepoint; never continue a broken transaction.
If a committed lifecycle transition can outlive a refused declaration, retain
retryable intent in its existing durable task owner, as organizer lifecycle alerts
do; a catch-and-log or `on_commit` callback alone cannot guarantee recovery.

Use stable server-derived identity for the milestone and qualifying participation.
Registration keys must not use a range ID: a later range allocation must not send
another registration confirmation. A new participation UUID is a new enrollment.
Retry a scheduled declaration with its persisted due instant, not a newly computed
`now` (the replay validator rejects changed meaning). Preserve pinned revisions,
snapshot identity and command idempotency keys. Range notices need a real current
generation binding, not merely a range ID embedded in a campaign UUID:
`participant_notice` currently does not set `CommunicationIntent.range_generation_ref`,
and worker preflight does not compare a released generation with the live range.
Do not advertise stale-range fencing from occurrence naming alone.

When email is unavailable, preserve the email declaration and truthful
`channel_unavailable` state, as `stage_notice` already does. Do not substitute
in-app delivery or manufacture accepted outcomes. Recovery must resume the same
intended occurrence explicitly under existing lateness/retention rules; enabling
a backend must not replay every historical draft. After release, provider
acceptance, partial/failed/withdrawn/empty outcomes and read/acknowledgement remain
separate. Stable Message-ID does not remove the at-least-once duplicate window
after provider acceptance and before database settlement (ADR-071).

## Cancellation specialization required by #662

ADR-051-R6 currently forbids new materialization for a cancelled event. Its
implementing change must document a narrow specialization in that ADR's registry
entry, with behavior evidence across **all** of these existing owners:

- Only the internal, fixed-content cancellation declaration for the actual
  committed transition, with one exact event, participant audience and stable
  occurrence, may pass. Neither a supplied origin nor `NoticeKind` is authority.
  Organizer authoring must remain unable to select that system notice.
- `event._lifecycle.cancel_event`, `_cancel_event_tasks` and
  `communication.lifecycle.on_event_cancelled` must stop ordinary work without
  consuming the new cancellation declaration/task. Persist that declaration with
  cancellation; do not release a generic announcement before cancelling as a race
  workaround. A repeat cancellation must not erase the original notice or resend.
- `release._assert_release_allowed` and due release must admit only that proven
  cancellation occurrence after cancellation. `delivery._fence_reason` must apply
  the identical narrow distinction before I/O. Broad `system=True` exemptions
  would enable unrelated messages after cancellation and are prohibited.
- Campaign cancellation, participant removal/banning, erased coordinates,
  retention, exact tenant/event scope, retry ceilings and lease fencing still
  apply. Ordinary multi-event work remains stopped only for its cancelled event.

This preflight does not activate an exception or weaken the current fence.
Until #662 satisfies these obligations, cancellation email remains a known gap.

## Cross-cutting owners and gates

| Layer in scope | Canonical incumbent and required satisfaction |
| --- | --- |
| Authentication and authority | Existing organizer controllers, `ctf.services.authorization`/`unified_authorization`, native `CredentialContext`, exact token action ceilings, session CSRF and `admin_external_audit`. Admit participant-management/lifecycle actions through their existing policy. A participant manager need not gain `event.manage_communications` to cause an automatic fixed confirmation. `AdmissionActor(system=True)` is internal proven lifecycle execution only; missing actor, event creator attribution, workspace role or email never supplies authority. Keep remote policy outside row locks. Public approval currently locks the request before nested participant work; reconcile that order with canonical event-first locking rather than copying it into another callback. |
| Parent/scope shapes | `workspaces.services.resource_scope_from_ids`/`resolve_resource_scope`, communication admission and immutable target sets. Preserve exact account/organization/workspace placement; support existing account-scoped events as well as workspace events. Do not add a workspace-only shortcut or import Workspaces/CMS/Engine models. Current native contracts supersede historical legacy-user/workspace preflights. |
| Input, domain and database validation | Existing registration/participant serializers, `ClosedSerializer` and `CommunicationJSONParser` for communication APIs, `ctf.communication_contracts`, `CTFBaseModel.full_clean`, model immutability and database constraints. Keep transport fields and system kind out of public input. New presentation kinds extend `NoticeKind`, the model choices and check-constraint migration together; values must fit `notice_kind.max_length=16` (for example `registration` and `event_cancelled`). Queryset/bulk writes bypass model validation. No duplicate DTO/schema, email regex or exception family. |
| Persistence/concurrency | Django ORM and existing campaign/revision/intent/snapshot/attempt models, `release`, `scheduling`, `materialize`, `backpressure`, `delivery` and `disposition`. Event-first deterministic locks, uniqueness, skip-locked claims, lease tokens, one aggregate derivation, bounded budgets, no locks across mail I/O. No new repository layer, outbox or broker. |
| Recipient privacy/lifecycle | `audience.recipient_queryset`, `viewing_participant_q`, `recipient_fence`, `on_participant_removed`, account purge and `retention`. Viewing eligibility differs from competing/ranking eligibility; never copy a status tuple. Resolve the participant's contact email, not the isolated user's intentionally empty email. `EncryptedStringField` stores only the captured coordinate; only the email adapter decrypts its bound snapshot. Removal/purge erases it and retries never retarget. |
| Content, URLs and MIME | `validate_message_content`, immutable `MessageRevision`, `NoticeKind`, trusted `templates/ctf/email/notice/` pairs and `EmailAdapter`. Reuse bounded subject/Markdown, scalar context, HTML autoescape and inert plain text; no stored template overrides, `mark_safe`, organizer HTML or template compilation. Use snapshot-event facts and existing timezone formatting. `shared.site_url.validated_site_url` supplies the origin, never request Host or the legacy unvalidated login URL builder. Subject folding and Django header/address validation remain at the existing adapter/transport seams. One recipient per message. |
| Transport/errors | `shared.email.attempt_email`, `email_classification`, `email_deadline` and `DeliveryOutcome` already provide one real bounded attempt, closed outcomes/reasons and validated optional receipts. Existing worker owns retry/backoff/shutdown. Reuse `CTFCommunicationError`, `ctf_error_response`, `shared.api.errors`/`shared.errors` for public envelopes; never expose address, rejected content, provider response, traceback or exception cause. |
| Configuration and env binding | No new setting is needed for a milestone. Keep `config/_email.py`, `_runtime_env.py`, `_ctf_communication_settings.py`, `_product_env.py` and `_env_manifest.py` authoritative. Availability must agree in web/scheduler/worker; deployed console/file/dummy backends cannot send or count as accepted. If wiring changes, pass `shifter/installation/contract/`, published backend-bundle shape, runtime inventories, `scripts/gcp/render_runtime_env.py`, `scripts/bootstrap/aws_eks.py` and existing Terraform inputs. Use canonical `BIGRAE_*` product env ingress with conflict-checked compatibility; regenerate generated artifacts through existing tools. |
| Secrets and host/OS exposure | `entrypoint.sh`/`entrypoint-lib.sh` hydrate field keys and optional `EMAIL_API_KEY_SECRET_ID` JSON (`api_key`) via the established secret-store/stdin boundary. Stable field keys are required across retries/redeploys. AWS uses workload IAM; configured GCP ESP credentials stay in runtime environment, never argv, URLs, task metadata, logs, manifests, generated config or Terraform state. No new destination/provider/service is authorized. No shell/CLI invocation or secret loading belongs in CTF milestone code. |
| Deployed runtime | Existing `run_ctf_scheduler`, `drain_ctf_communication_deliveries`, chart worker Deployment, `values.schema.json`, security/proxy/tmp helpers, worker service account, database duties, NetworkPolicy and provider IAM. Adding a producer needs no new deployment. Existing configuration is not proof of working mail: worker identity must have coordinate-key access, database access, selected-backend egress and send permission. Keep non-root restrictions, digest-pinned images, heartbeat/shutdown/deadline relationships and provisioner privilege separation. |
| Observability and evidence | `communication.metrics`, `reporting`, `disposition`, `shared.audit`, `RequestIDMiddleware`, `shared.log_sanitize` and `config.logging.ECSFormatter`. Log IDs plus closed channel/status/reason and supported correlation fields; metrics use bounded labels. `safe_log_value` prevents log injection but does not redact PII; existing lifecycle `logger.exception` calls are not a safe precedent for provider errors. Strict audit is body-free evidence, never a queue. Metrics/log publication failure cannot cause resend. |
| Repository/API workflow | `.ground-control.yaml` owns repository identity; `.importlinter`, ADR registry/exceptions, `.gc/plan-rules.md`, `.pre-commit-config.yaml` and `.github/quality-path-filters.yaml` retain enforcement ownership. No new route or public schema is necessary. If an API shape changes, use `manage.py api_contract` and `npm --prefix frontend run gen:api`, ADR-040 compatibility and existing operation-policy/scope inventories; never hand-edit generated contracts. Requirement traceability must describe actual ledger code/evidence and keep legacy rows historical. |

## Extension seam, evidence and non-goals

The next milestone belongs in the existing server-only notice-kind/content
selection and canonical lifecycle declaration seam. Keep presentation kind,
milestone occurrence, audience and channel as their existing separate concepts:
no configurable callable, template path, recipient address or generic
`ignore_cancellation` switch. New kinds use trusted template pairs; transport
provider variation remains `EMAIL_BACKEND` behind `shared.email`, locale remains
revision data, and retry policy stays typed worker configuration. Registration
requires no range and should remain independent of future range recovery.

Implementation evidence should extend existing `tests/ctf/test_participant_accounts.py`,
`test_public_registration_organizer_api.py`, `test_services/test_participant.py`,
`test_notification_wave.py` and `test_communication_{email_adapter,scheduling,
lifecycle_wiring,admission_postgres,delivery_postgres,email_restart_postgres}.py`.
Exercise real services/transactions, substituting external email only: approval
versus anonymous submission; all seat paths; rollback and nested-commit crash;
replay/concurrent registration; no-address/unsafe content; no transport on the
request path; cancellation fences and removal races; retry/restart and unavailable
backend; ready signal before polling and no polling; final-score order/access;
bounded failures without PII in errors/logs. Existing mock-only callback tests
do not establish durable producer coverage. Reuse shared email/config tests and
runtime/chart contracts if those boundaries change. Run the full ADR guard;
Python changes also need Ruff/import-linter and appropriate service/PostgreSQL
tests, with chart/Terraform/workflow checks only when touched.

Non-goals: implementing CTF-801 in this preflight; reviving `CTFNotification`,
`SEND_NOTIFICATION`, `_send_email`, `send_announcement` or credential resend;
workspace invitation/reset email semantics (ADR-071 deliberately uses a different
synchronous contract there); password/token delivery or retention; new organizer
alerts, signup flows, public results/access policy, template editors, localization
systems, attachments, provider/bounce/open tracking, mailbox guarantees, or a
general notification framework. #664 must not silently absorb #662 or declare
range/results gaps solved by registration-only evidence.
