# CTF Invite Token Account-Linking Preflight (#469)

Status: pre-implementation guidance

Date: 2026-06-27

Issue: GitHub #469, "CTF invite tokens remain reusable full-account magic links
until event end"

This issue is requirement-free. The GitHub issue title, body, and acceptance
criteria are the shipping contract. This note is intentionally not an
implementation plan.

## Scope Boundary

Treat this as hardening the existing CTF participant invite and registration
lifecycle. Do not replace the platform identity provider, regular Django
sessions, CTF scoring, range provisioning, or notification rendering.

Keep these concepts separate:

1. Invite credential: `CTFParticipant.invite_token`, expiry, and consumed state.
2. Invite delivery: the fragment URL built by
   `ctf.services.notification._build_registration_url()`.
3. Invite exchange: the unauthenticated, CSRF-protected POST that validates and
   consumes the credential.
4. Participant onboarding: linking or creating a Django user after the invite
   holder has proved possession of the invitation.
5. Full-account authentication: normal Django/OIDC/Identity Platform login for
   pre-existing accounts and their existing privileges.

## Architectural Decisions

- Invite tokens are one-time credentials. A successful exchange must make the
  submitted token unusable in the same persistence critical section that decides
  the token is valid.
- `MAGIC_LINK_SINGLE_USE=False` must not remain a production behavior for CTF
  invite login. If the knob is kept for compatibility, the safe default is
  one-time use and disabling it must not be needed for the normal CTF flow.
- Do not clear `invite_token` to a shared empty string while the column remains
  unique. Either change the persistence shape deliberately, or replace the token
  with a fresh unguessable burned value and mark it expired or consumed.
- Participant creation/import should create invited participant rows only. It
  must not call `_auto_register_participant()` or otherwise link a Django `User`
  before the invite holder uses the invitation.
- The invite token must not be a full-account magic login for a pre-existing
  Django account. For an existing user email, the invite exchange may enroll the
  participant only after the request is already authenticated as that same user,
  or after an explicit second confirmation path through the normal login system.
- Creating a new participant-only Django user remains acceptable after token
  possession is proven, as long as the resulting account receives only the
  existing CTF participant profile/group state and no staff, superuser, Threat
  Research, CMS authoring, or organizer privileges.
- Keep the #1088 transport decision: invite URLs carry the token in the fragment
  and the browser POSTs JSON to the exchange endpoint. Do not reintroduce query
  parameters, path-segment tokens, hidden server-rendered token fields, or raw
  token template context.
- Registration-denial responses must be fixed, bounded messages. They must not
  reveal whether a submitted token belongs to a staff, organizer, existing user,
  newly invited user, consumed token, or database row beyond what the UX strictly
  needs.

## Incumbents To Reuse

| Concern | Canonical incumbent | Guardrail for #469 |
| --- | --- | --- |
| Invite token model | `CTFParticipant.invite_token`, `invite_token_expires`, `is_invite_valid` | Extend or tighten this state; do not add a parallel invite table unless deliberately migrating the credential model. |
| Participant lifecycle | `ctf.services.participant.lifecycle`, `invite_participant()`, `bulk_import_participants()`, `resend_invite()` | Keep invite creation, refresh, consumption, and onboarding decisions in this service area, not duplicated in views/templates. |
| Participant eligibility | `eligible_participant_q()`, `is_active_participant()`, `get_participant_by_user()` | Only registered, non-disqualified rows become playable and scoreable. Do not create a second eligibility predicate for newly onboarded invitees. |
| User/profile/group mutation | `_set_ctf_participant_profile()`, `_clear_ctf_participant_profile()`, `management.services.set_active_ctf_event`, `shared.auth.CTF_PARTICIPANT_GROUP` | Reuse existing profile and group helpers; do not mutate CTF groups from the view or add a new RBAC mapping. |
| Auth/session creation | Django `login(...)` with `ModelBackend`, existing OIDC/Identity Platform backends for normal login | The invite exchange is not a replacement auth backend for pre-existing accounts. |
| Token delivery | `_build_registration_url()`, `SITE_URL`, `reverse("ctf:ctf_register")`, #1088 fragment transport | Keep one URL builder and one transport contract. |
| Request shape validation | `ctf.views._parsing._parse_body_object()`, `_get_body_str()` | Reuse the existing JSON body gates and bounded token length check. |
| CSRF | `ensure_csrf_cookie`, Django `CsrfViewMiddleware`, `ctf-register.js` `X-CSRFToken` pattern | Do not mark the session-creating exchange `csrf_exempt`. |
| Runtime config | `config/_oidc_settings.py`, `config/env-manifest.json`, runtime renderers for `SITE_URL` and `MAGIC_LINK_*` | Avoid new env knobs. If a default or knob changes, update the manifest and runtime docs/tests. |
| Logging | module loggers, `shared.log_sanitize.safe_log_value`, `safe_log_fingerprint`, `config.logging.ECSFormatter` | Never log raw invite tokens, request bodies, cookies, CSRF tokens, or full magic-link URLs. |
| Error envelopes | existing CTF JSON `{"error": ...}` style, `shared.errors.classify_user_message` where exception text might leak | Return authored messages; keep exception details server-side and sanitized. |
| Import boundaries | `.importlinter`, `ctf.bridges`, `shared.auth`, `management.services` | Do not make CTF depend on Mission Control or Engine, and do not hide boundary violations in a new helper. |
| Tests | `tests/ctf/test_auth_registration.py`, `tests/ctf/test_participant_views.py`, `tests/ctf/test_services/test_participant.py`, `tests/ctf/test_services/test_notification.py` | Replace tests that encode multi-use or invite-time auto-registration; cover concurrent token reuse and existing-account onboarding. |

## Cross-Cutting Layers

- Auth surface: `/ctf/register/` remains public and performs no login. The POST
  exchange remains public, CSRF-protected, and credential-gated. It may create a
  normal Django session only for a newly created participant-only user. Existing
  staff, organizer, superuser, or standard accounts must authenticate through
  the normal provider before the invite can add CTF participation; the invite
  exchange must not create that existing account's session.
- Secret-handling surface: invite tokens, session cookies, CSRF tokens, email
  URLs, request bodies, and pending-onboarding handles are secret-bearing. Keep
  them out of query strings, logs, audit rows unless redacted, GitHub comments,
  shell commands, process argv, ConfigMaps, Terraform output, screenshots,
  browser storage, and analytics.
- URL/referrer surface: preserve the #1088 fragment transport and
  `Referrer-Policy: no-referrer`. The token may be present briefly in the
  address bar, then scrubbed by `ctf-register.js`; it must not appear in
  `request.GET`, `Location` headers, server-rendered context, redirects, or
  error messages.
- Request validation surface: the exchange body must be a JSON object, `token`
  must be a string, and oversized or malformed input must fail before database
  lookup. Reuse `ctf.views._parsing` rather than endpoint-local parsing.
- Persistence and concurrency surface: consume under `transaction.atomic()` and
  a row-level lock on the participant selected by token. Recheck expiry,
  consumed state, linked-user state, participant status, and matching user/email
  while holding the lock so two concurrent exchanges cannot both create
  sessions from the same token.
- User/profile persistence surface: setting `user`, `status`,
  `registered_at`, CTF participant group membership, and `active_ctf_event` is a
  participant-onboarding operation. Use the existing lifecycle/profile helpers
  and update all invite entry points consistently.
- Config/env surface: `MAGIC_LINK_EXPIRY_HOURS`, `MAGIC_LINK_SINGLE_USE`, and
  `SITE_URL` are already in settings and the env manifest. This issue should
  not add provider-specific runtime variables. Any default change must be
  reflected in `config/env-manifest.json` and config tests.
- OS/runtime exposure: fixes must not move tokens into management-command
  arguments, deployment manifests, generated env files, Kubernetes values,
  Terraform outputs, process-global debug state, or shell-visible diagnostics.
- Error-envelope surface: missing, invalid, expired, consumed, existing-account,
  and authorization-failed cases should use fixed responses and should not
  support token probing or account enumeration.
- Observability surface: log low-cardinality outcomes with sanitized participant
  or event IDs only when needed. Use `safe_log_fingerprint()` if correlating a
  sensitive token-like value is truly required.

## Extensibility

The extension point belongs at the participant lifecycle token-consumption
boundary. The obvious future variation is a no-JavaScript/manual-code fallback
or a server-side pending-invite handle for existing-account confirmation. That
should be a mode on the same invite exchange/onboarding contract, not a second
notification path, second credential model, or second participant eligibility
predicate.

The existing-account policy should accept the current authenticated user as an
input to the lifecycle operation. That leaves room for additional identity
providers or a stronger confirmation step without rewriting invite creation,
email rendering, scoring, or CTF access control.

## Whole-Repo Scope

Likely in scope for implementation:

- `shifter/shifter_platform/ctf/models/team.py` and migrations if consumed
  state, nullable tokens, or token uniqueness semantics change.
- `shifter/shifter_platform/ctf/services/participant/lifecycle.py` and
  `bulk_import.py` for invite-time registration removal and token consumption.
- `shifter/shifter_platform/ctf/views/participant.py`,
  `templates/ctf/participant/register.html`, and `static/js/ctf-register.js`
  for exchange behavior and existing-account confirmation UX.
- `shifter/shifter_platform/ctf/services/notification.py` only to preserve or
  parameterize the canonical URL builder.
- `shifter/shifter_platform/config/_oidc_settings.py` and
  `config/env-manifest.json` if magic-link defaults or semantics change.
- Tests under `shifter/shifter_platform/tests/ctf/` matching the touched
  lifecycle, view, notification, and concurrency seams.
- `changelog.d/469.security.md` for the implementation PR.

Usually out of scope:

- Replacing OIDC, Identity Platform, Django sessions, or platform login.
- Changing participant scoring, team behavior, range provisioning, event
  lifecycle, CTFd sync, or notification template rendering beyond the invite
  variables already exposed.
- Adding a new auth backend, exception hierarchy, DTO package, notification
  renderer, audit model, or cross-app magic-link framework.
- Broadening `ALLOWED_HOSTS`, CSRF trusted origins, WAF/ingress behavior, or
  `SITE_URL` semantics to make invite exchange work.
- Hashing stored invite tokens as a prerequisite for this fix. It is a valid
  future hardening step, but this issue is about one-time use and account-link
  semantics.

## Gotchas And Anti-Patterns

- Do not ship a view-only fix that clears the token after `login(...)` without a
  transaction and row lock; concurrent requests can reuse the token.
- Do not preserve the current invite/import call to `_auto_register_participant()`
  and call the issue fixed just because tokens become one-time.
- Do not keep tests that assert "same token exchanged twice logs in both times";
  that is the vulnerability contract.
- Do not link a staff, organizer, superuser, or any existing account solely
  because its email matches the invited participant row.
- Do not use the invite token as proof of authority to inherit all existing
  account privileges.
- Do not store raw tokens in anonymous sessions, local storage, session storage,
  cookies, logs, audit detail blobs, or template context.
- Do not introduce a second definition of "registered participant." The
  repository already treats `registered_at` plus an eligible status as the
  access/scoring predicate.
- Do not add a permissive compatibility switch that restores reusable invite
  tokens in production.
