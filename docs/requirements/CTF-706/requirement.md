---
id: CTF-706
title: "Event Cancellation"
status: ACTIVE
type: FUNCTIONAL
priority: MUST
wave: 2
created_at: 2026-03-18T05:28:23.257931Z
updated_at: 2026-10-31T00:00:00Z
---

# CTF-706: Event Cancellation

## Statement

The system shall support cancelling events from any pre-ended state (draft, registration, active, paused). Cancellation shall: transition the event to a cancelled state, trigger cleanup of all provisioned resources, send cancellation notifications to all registered participants, and prevent further submissions. Cancelled events shall be visible in event history but not in active listings.

## Rationale

Events get cancelled due to insufficient registrations, scheduling conflicts, or infrastructure issues. Without a cancellation flow, organizers must manually notify participants and clean up resources. The cancelled state preserves audit trail while clearly distinguishing from normally ended events.

## Traceability

- IMPLEMENTS → GITHUB_ISSUE `662` (Issue #662 - cancellation notice to registered participants through the communication ledger)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/event/_lifecycle.py` (cancel_event: cancelled state, communication fencing and range cleanup)
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_event_lifecycle.py` (Cancellation from the draft, registration, scheduled and paused states)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/notification/delivery_milestones.py` (declare_cancellation_notice: fixed participant cancellation notice)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/notification/ledger.py` (declare_notice: durable declaration in the cancel transaction)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/communication/campaigns.py` (is_event_cancellation_notice and the cancelled-event precondition)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/communication/release.py` (cancelled-event release fence exception)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/communication/delivery.py` (cancelled-event delivery fence exception)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/templates/ctf/email/notice/cancellation.html` (cancellation email template pair)
- IMPLEMENTS → DOCUMENTATION `docs/architecture/ctf-cancellation-notice-design-662.md` (cancellation notice design)
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_event_lifecycle_api.py` (cancellation notice through the real ledger, release task and worker; fence exception scope)
