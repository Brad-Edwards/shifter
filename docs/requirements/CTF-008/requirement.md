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

# CTF-008 — Notifications & Communications

## Statement

The CTF layer should keep participants informed of event milestones and organizer communications through the platform's email service (PLAT-103) and optional real-time in-app notifications via the platform's WebSocket infrastructure (PLAT-105).

## Rationale

Communication keeps participants informed and engaged before, during, and after events. Email notifications are critical for Shifter consultants who may not be actively watching the platform when events start or ranges become available. CTF defines the notification triggers and content; the platform provides the delivery infrastructure.

## Traceability

- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/notification.py` (CTF Notification service (email: invitations, credentials, reminders, announcements, scheduling))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/models.py (CTFNotification)` (CTFNotification model (type, status, scheduling, recipient filtering, sent tracking))
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/views.py (notification views)` (Notification views: admin_notification_list, admin_notification_create, api_notification_list, api_notification_send, api_send_invitations)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/templates/ctf/email/` (Email templates: invitation, credentials, reminder, announcement (HTML + text))
- TESTS → TEST `shifter/shifter_platform/ctf/tests/test_services/test_notification.py` (Notification service tests (invitations, credentials, reminders, announcements, scheduling, rendering))
