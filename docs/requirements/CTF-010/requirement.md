---
id: CTF-010
title: "Scheduled Tasks & Automation"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-03-18T05:28:21.230446Z
updated_at: 2026-03-26T06:33:55.418204Z
---

# CTF-010: Scheduled Tasks & Automation

## Statement

The CTF layer should automate time-sensitive event operations, such as range provisioning, resource cleanup, and state transitions, using the platform's scheduler framework (see CTF-1001). Events run reliably without requiring organizer presence at exact moments.

## Rationale

CTF events have time-bound lifecycles that require actions at specific moments, ranges must be ready before start, torn down after end, reminders sent in advance. Manual execution is error-prone and requires organizer availability at exact times. CTF registers its task types with the platform scheduler rather than building its own automation infrastructure.

## Traceability

- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/models/notification.py` (CTFScheduledTask model (task_type, scheduled_for, status, and lifecycle transitions))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/management/commands/run_ctf_scheduler.py` (CTF scheduler management command (poll loop, signal handling, heartbeat, stale task recovery, task dispatch))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/event/scheduling.py` (Event task scheduling, rescheduling, and cancellation)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/enums.py (ScheduledTaskType, ScheduledTaskStatus)` (Scheduled task enums: SPIN_UP_RANGES, CLEANUP_RANGES, SEND_REMINDER, EVENT_START, EVENT_END + status lifecycle)
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_services/test_scheduler_handlers.py` (CTF scheduler handler tests - task dispatch, lifecycle handlers, stale recovery, reminders, release and cleanup)
- IMPLEMENTS → GITHUB_ISSUE `539` (Issue #539 - automated coverage and Ground Control TESTS trace links for active CTF requirements)
- TESTS → TEST `shifter/shifter_platform/tests/cms/test_reconcile_range_events.py` (Canonical reconciler lease-expiry tests)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/management/commands/reconcile_range_events.py` (Canonical range event and lease reconciliation runtime)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/cms/services/_range_lease.py` (Server-owned range lease and expiry lifecycle)
- IMPLEMENTS → ADR `ADR-051` (Timed communications converge on immutable intents executed by the existing CTF scheduler)
- IMPLEMENTS → DOCUMENTATION `docs/architecture/ctf-communications-raes-inject-preflight-2047.md` (Timed, lifecycle, RAES, and range-signal trigger realization contract)
- IMPLEMENTS → GITHUB_ISSUE `2047` (Issue #2047 - communications trigger and scheduler architecture)
