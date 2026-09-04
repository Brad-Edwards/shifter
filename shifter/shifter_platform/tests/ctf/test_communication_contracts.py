"""Closed audience/trigger/channel shapes and the safe content profile (ADR-051, #2048).

Pure, DB-free validators for the communication domain. Every test drives the real
validator and asserts the effect (accept the safe shape, reject the hostile one),
so it goes red if a bound or a rejection rule is removed. These cover the issue's
"content bounds" and closed-vocabulary acceptance criteria.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ctf.communication_contracts import (
    CONTENT_PROFILE_V1,
    MAX_BODY_BYTES,
    MAX_SUBJECT_CODEPOINTS,
    validate_audience_spec,
    validate_channels,
    validate_message_content,
    validate_trigger_spec,
)
from ctf.exceptions import CTFCommunicationError

ALLOWED_HOSTS = frozenset({"docs.example.com"})


# ---------------------------------------------------------------------------
# Message content: ctf-communication-markdown/v1
# ---------------------------------------------------------------------------


def _content(**overrides):
    data = {
        "subject": "Welcome to the event",
        "body": "# Hello\n\nRead the **rules** and good luck.",
    }
    data.update(overrides)
    return data


def test_valid_markdown_content_is_accepted_and_digested():
    result = validate_message_content(_content(), allowed_link_hosts=ALLOWED_HOSTS)

    assert result["subject"] == "Welcome to the event"
    assert result["profile"] == CONTENT_PROFILE_V1
    assert result["digest"].startswith("sha256:")


def test_empty_subject_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(subject="   "), allowed_link_hosts=ALLOWED_HOSTS)


def test_oversized_subject_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(subject="x" * (MAX_SUBJECT_CODEPOINTS + 1)), allowed_link_hosts=ALLOWED_HOSTS)


def test_control_characters_in_subject_are_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(subject="bad\x07subject"), allowed_link_hosts=ALLOWED_HOSTS)


def test_oversized_body_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body="a" * (MAX_BODY_BYTES + 1)), allowed_link_hosts=ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        "Hello <div>raw html</div>",
        "<img src=x onerror=alert(1)>",
        "<iframe src='https://evil.example'></iframe>",
    ],
)
def test_raw_html_is_rejected(body):
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body=body), allowed_link_hosts=ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "body",
    [
        "[click](javascript:alert(1))",
        "[data](data:text/html;base64,PHNjcmlwdD4=)",
        "[file](file:///etc/passwd)",
        "[shell](vbscript:msgbox)",
    ],
)
def test_dangerous_url_schemes_are_rejected(body):
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body=body), allowed_link_hosts=ALLOWED_HOSTS)


def test_external_link_to_disallowed_host_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(
            _content(body="See [here](https://evil.example.com/x)"), allowed_link_hosts=ALLOWED_HOSTS
        )


def test_external_link_to_allowed_host_is_accepted():
    result = validate_message_content(
        _content(body="See [docs](https://docs.example.com/guide)"), allowed_link_hosts=ALLOWED_HOSTS
    )
    assert result["digest"].startswith("sha256:")


def test_relative_link_is_accepted():
    result = validate_message_content(_content(body="See [rules](/events/rules)"), allowed_link_hosts=ALLOWED_HOSTS)
    assert result["digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "url",
    [
        "https://192.168.0.1/x",  # IP literal
        "https://user:pass@docs.example.com/x",  # credentials
        "http://docs.example.com/x",  # non-https external
        "https://localhost/x",  # localhost
    ],
)
def test_unsafe_link_hosts_are_rejected(url):
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body=f"[x]({url})"), allowed_link_hosts=ALLOWED_HOSTS)


def test_identical_content_produces_a_stable_digest():
    first = validate_message_content(_content(), allowed_link_hosts=ALLOWED_HOSTS)
    second = validate_message_content(_content(), allowed_link_hosts=ALLOWED_HOSTS)
    assert first["digest"] == second["digest"]


def test_protocol_relative_link_is_rejected():
    # `//host` is resolved by browsers as an external origin, so it must not slip
    # through as a "relative" path (codex security finding).
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body="[login](//attacker.example/login)"), allowed_link_hosts=ALLOWED_HOSTS)


def test_backslash_in_link_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body="[x](/\\attacker.example)"), allowed_link_hosts=ALLOWED_HOSTS)


def test_reference_style_link_to_disallowed_host_is_rejected():
    body = "See [the rules][r] for details.\n\n[r]: https://attacker.example/phish"
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body=body), allowed_link_hosts=ALLOWED_HOSTS)


def test_reference_style_link_to_allowed_host_is_accepted():
    body = "See [the docs][d].\n\n[d]: https://docs.example.com/guide"
    result = validate_message_content(_content(body=body), allowed_link_hosts=ALLOWED_HOSTS)
    assert result["digest"].startswith("sha256:")


def test_bare_url_to_disallowed_host_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(_content(body="Visit https://attacker.example now"), allowed_link_hosts=ALLOWED_HOSTS)


def test_image_destination_to_disallowed_host_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_message_content(
            _content(body="![logo](https://attacker.example/x.png)"), allowed_link_hosts=ALLOWED_HOSTS
        )


# ---------------------------------------------------------------------------
# Audience spec (closed selector)
# ---------------------------------------------------------------------------


def test_single_participant_audience_is_accepted():
    spec = {"kind": "participant", "participant_ids": [str(uuid4())]}
    result = validate_audience_spec(spec)
    assert result["kind"] == "participant"


def test_multi_event_audience_requires_at_least_two_events():
    with pytest.raises(CTFCommunicationError):
        validate_audience_spec({"kind": "multi_event", "event_ids": [str(uuid4())]})


def test_team_audience_is_accepted():
    result = validate_audience_spec({"kind": "team", "team_ids": [str(uuid4()), str(uuid4())]})
    assert result["kind"] == "team"


def test_audience_rejects_email_addresses():
    with pytest.raises(CTFCommunicationError):
        validate_audience_spec({"kind": "participant", "emails": ["a@b.com"]})


def test_audience_rejects_unknown_keys():
    with pytest.raises(CTFCommunicationError):
        validate_audience_spec({"kind": "event", "event_ids": [str(uuid4())], "sql": "1=1"})


def test_audience_rejects_non_uuid_identifiers():
    with pytest.raises(CTFCommunicationError):
        validate_audience_spec({"kind": "participant", "participant_ids": ["not-a-uuid"]})


def test_audience_rejects_unknown_kind():
    with pytest.raises(CTFCommunicationError):
        validate_audience_spec({"kind": "everyone"})


# ---------------------------------------------------------------------------
# Trigger spec (closed declaration)
# ---------------------------------------------------------------------------


def test_manual_trigger_is_accepted():
    assert validate_trigger_spec({"kind": "manual"})["kind"] == "manual"


def test_absolute_time_trigger_requires_a_due_time():
    result = validate_trigger_spec({"kind": "absolute_time", "due_at": "2026-10-01T00:00:00Z"})
    assert result["kind"] == "absolute_time"


def test_absolute_time_trigger_without_due_time_is_rejected():
    with pytest.raises(CTFCommunicationError):
        validate_trigger_spec({"kind": "absolute_time"})


def test_trigger_rejects_unknown_kind_and_extra_keys():
    with pytest.raises(CTFCommunicationError):
        validate_trigger_spec({"kind": "manual", "callable": "os.system"})
    with pytest.raises(CTFCommunicationError):
        validate_trigger_spec({"kind": "webhook"})


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def test_channels_accepts_a_valid_subset():
    assert validate_channels(["in_app", "email"]) == ["in_app", "email"]


def test_channels_rejects_empty():
    with pytest.raises(CTFCommunicationError):
        validate_channels([])


def test_channels_rejects_duplicates_and_unknown():
    with pytest.raises(CTFCommunicationError):
        validate_channels(["in_app", "in_app"])
    with pytest.raises(CTFCommunicationError):
        validate_channels(["carrier_pigeon"])
