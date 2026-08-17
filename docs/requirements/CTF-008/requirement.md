---
id: CTF-008
title: "Notifications & Communications"
status: ACTIVE
type: FUNCTIONAL
priority: SHOULD
wave: 2
created_at: 2026-03-18T05:28:21.159679Z
updated_at: 2026-03-26T06:33:52.781713Z
---

# CTF-008: Notifications & Communications

## Statement

The CTF layer should keep participants informed of event milestones and organizer communications through the platform's email service (PLAT-103) and optional real-time in-app notifications via the platform's WebSocket infrastructure (PLAT-105).

## Rationale

Communication keeps participants informed and engaged before, during, and after events. Email notifications are critical for Shifter consultants who may not be actively watching the platform when events start or ranges become available. CTF defines the notification triggers and content; the platform provides the delivery infrastructure.

## Traceability

- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/notification/` (CTF notification services for organizer authoring, participant delivery, email, scheduling, realtime fan-out, cleanup, and delivery milestones)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/models/notification.py` (CTFNotification and notification scheduling state)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/api/organizer/notifications.py` (Canonical organizer notification API)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/views/admin_notifications.py` (Server-rendered notification administration)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/views/api/notifications.py` (Legacy notification API compatibility surface)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/templates/ctf/email/` (Email templates: invitation, credentials, reminder, announcement (HTML + text))
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_services/test_notification.py` (Notification service tests (invitations, credentials, reminders, announcements, scheduling, rendering))
- IMPLEMENTS → ADR `ADR-051` (Secure scoped CTF communications and RAES inject realization boundary)
- IMPLEMENTS → DOCUMENTATION `docs/architecture/ctf-communications-raes-inject-preflight-2047.md` (Communication domain, authorization, delivery, content-safety, and in-range trigger architecture)
- IMPLEMENTS → GITHUB_ISSUE `2047` (Issue #2047 - secure scoped participant communications and RAES inject realization architecture)
