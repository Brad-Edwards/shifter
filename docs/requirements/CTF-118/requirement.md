---
id: CTF-118
title: "Programmable Flag Validation"
status: ACTIVE
type: FUNCTIONAL
priority: COULD
wave: 2
created_at: 2026-03-18T20:38:48.685011Z
updated_at: 2026-03-26T06:36:52.548888Z
---

# CTF-118 — Programmable Flag Validation

## Statement

The system could support programmable flag validation where custom code or an HTTP callback determines whether a submission is correct. Programmable flags shall support: code-based validators (custom logic executed server-side) and HTTP-based validators (submission forwarded to an external endpoint that returns pass/fail). Validator configuration shall be per-flag.

## Rationale

Programmable flags enable dynamic validation logic — e.g., verifying a submission against the participant's specific range state, checking that an exploit actually worked on the target VM, or validating flags that change per participant. HTTP-based validators allow delegation to external services for validation, which is important for range-integrated challenges. (CTFd supports Programmable and HTTP flag types beyond static and regex.)

## Traceability

- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/validators.py` (Validator registry and HTTP validation module)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/services/challenge.py` (Challenge service - programmable/http flag verification and creation)
- IMPLEMENTS → CODE_FILE `shifter/shifter_platform/ctf/models.py` (CTFFlag model - programmable/http flag_type and validator_config field)
- TESTS → TEST `shifter/shifter_platform/tests/ctf/test_programmable_flags.py` (Programmable and HTTP flag validation tests)
- IMPLEMENTS → GITHUB_ISSUE `Brad-Edwards/shifter#509` (CTF-118: Programmable Flag Validation)
