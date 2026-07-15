"""Isolated temporary CTF participant account behavior (issue #1206)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from ctf.services.participant.accounts import (
    create_participant_accounts,
    purge_expired_participant_accounts,
    rename_participant_username,
    reset_participant_credentials,
)
from ctf.services.participant.lifecycle import disqualify_participant, invite_participant
from management.services import get_user_profile

from .conftest import TEST_CTF_BOOTSTRAP_PASSWORD

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_login_rate_limit_cache():
    caches["launch_rate_limit"].clear()


def test_account_creation_never_reuses_platform_user_by_email(ctf_event, standard_user, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)

    participants = create_participant_accounts(
        ctf_event.id,
        count=1,
        email=standard_user.email,
        display_name="CTF Seat",
    )

    participant = participants[0]
    assert participant.user_id != standard_user.id
    assert participant.user.email == ""
    assert participant.email == standard_user.email
    assert participant.user.check_password(TEST_CTF_BOOTSTRAP_PASSWORD)


def test_account_creation_marks_low_privilege_force_change_account(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)

    participant = create_participant_accounts(ctf_event.id, count=1)[0]
    profile = get_user_profile(participant.user)

    assert participant.email == ""
    assert participant.user.username.startswith("range-")
    assert profile.is_ctf_account is True
    assert profile.must_change_password is True
    assert profile.user_type == "ctf_participant"
    assert participant.user.is_staff is False
    assert participant.user.is_superuser is False
    assert set(participant.user.groups.values_list("name", flat=True)) == {"CTF Participant"}


def test_ctf_login_rejects_platform_accounts(client, standard_user):
    response = client.post(
        reverse("ctf:ctf_login"),
        {"username": standard_user.username, "password": "testpass123"},
    )

    assert response.status_code == 200
    assert "Invalid username or password" in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_ctf_login_forces_password_change(client, ctf_event_active, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]

    response = client.post(
        reverse("ctf:ctf_login"),
        {"username": participant.user.username, "password": TEST_CTF_BOOTSTRAP_PASSWORD},
    )

    assert response.status_code == 302
    assert response.url == reverse("ctf:ctf_change_password")
    assert str(participant.user_id) == client.session["_auth_user_id"]


def test_organizer_can_rename_participant_username(ctf_event, organizer_user, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event.id, count=1)[0]

    rename_participant_username(participant.id, "range-blue-team", actor=organizer_user)

    participant.user.refresh_from_db()
    assert participant.user.username == "range-blue-team"
    assert not User.objects.filter(username__iexact="range-blue-team").exclude(pk=participant.user_id).exists()


def test_ctf_account_boundary_denies_non_participant_surface(ctf_event_active, monkeypatch):
    from config.middleware import CTFAccountBoundaryMiddleware

    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]
    request = RequestFactory().get("/mission-control/")
    request.user = participant.user

    response = CTFAccountBoundaryMiddleware(lambda _request: HttpResponse("escaped"))(request)

    assert response.status_code == 403
    assert response.content == b"Forbidden"


def test_force_change_boundary_redirects_before_ctf_surface(ctf_event_active, monkeypatch):
    from config.middleware import CTFAccountBoundaryMiddleware

    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]
    request = RequestFactory().get("/ctf/range/")
    request.user = participant.user

    response = CTFAccountBoundaryMiddleware(lambda _request: HttpResponse("escaped"))(request)

    assert response.status_code == 302
    assert response.url == reverse("ctf:ctf_change_password")


def test_platform_password_backend_rejects_ctf_credentials(ctf_event_active, monkeypatch):
    from config.auth import PlatformModelBackend

    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]

    authenticated = PlatformModelBackend().authenticate(
        None,
        username=participant.user.username,
        password=TEST_CTF_BOOTSTRAP_PASSWORD,
    )

    assert authenticated is None


def test_single_invite_accepts_no_email_and_creates_isolated_account(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)

    participant = invite_participant(ctf_event.id, "", "Walk-in")

    assert participant.email == ""
    assert participant.user is not None
    assert participant.user.profile.is_ctf_account is True


def test_delivery_email_is_not_unique_identity(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)

    first = invite_participant(ctf_event.id, "shared@example.test", "First")
    second = invite_participant(ctf_event.id, "shared@example.test", "Second")

    assert first.user_id != second.user_id


def test_event_bootstrap_password_override_is_used(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    ctf_event.participant_password_override = "EventOnly-Password-42"
    ctf_event.save(update_fields=["participant_password_override", "updated_at"])

    participant = create_participant_accounts(ctf_event.id, count=1)[0]

    assert participant.user.check_password("EventOnly-Password-42")
    assert not participant.user.check_password(TEST_CTF_BOOTSTRAP_PASSWORD)


def test_credential_reset_restores_bootstrap_and_sends_two_messages(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    sent = []
    monkeypatch.setattr("ctf.services.notification._send_email", lambda **kwargs: sent.append(kwargs))
    participant = create_participant_accounts(
        ctf_event.id,
        count=1,
        email="delivery@example.test",
    )[0]
    participant.user.set_password("PrivateChangedPassword-42")
    participant.user.save(update_fields=["password"])
    profile = get_user_profile(participant.user)
    profile.must_change_password = False
    profile.save(update_fields=["must_change_password"])

    reset_participant_credentials(participant.id)

    participant.user.refresh_from_db()
    profile.refresh_from_db()
    assert participant.user.check_password(TEST_CTF_BOOTSTRAP_PASSWORD)
    assert profile.must_change_password is True
    assert len(sent) == 2
    assert participant.user.username in sent[0]["text_content"]
    assert TEST_CTF_BOOTSTRAP_PASSWORD not in sent[0]["text_content"]
    assert TEST_CTF_BOOTSTRAP_PASSWORD in sent[1]["text_content"]


def test_disqualification_anonymizes_temporary_account(ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event.id, count=1, email="private@example.test")[0]

    disqualify_participant(participant.id, "rules")

    participant.refresh_from_db()
    participant.user.refresh_from_db()
    profile = get_user_profile(participant.user)
    assert participant.email == ""
    assert participant.user.is_active is False
    assert participant.user.has_usable_password() is False
    assert participant.user.username.startswith("ctf-tombstone-")
    assert profile.is_ctf_account is True
    assert profile.anonymized_at is not None


def test_post_event_retention_purge_anonymizes_accounts(ctf_event_active, monkeypatch, settings):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]
    ctf_event_active.event_start = timezone.now() - timedelta(hours=2)
    ctf_event_active.event_end = timezone.now() - timedelta(hours=1)
    ctf_event_active.save(update_fields=["event_start", "event_end", "updated_at"])
    settings.CTF_PARTICIPANT_ACCOUNT_RETENTION_HOURS = 0

    count = purge_expired_participant_accounts()

    participant.user.refresh_from_db()
    assert count == 1
    assert participant.user.is_active is False


def test_ctf_login_rate_limits_repeated_failures(client, standard_user, settings):
    settings.CTF_LOGIN_RATE_LIMIT_MAX = 2
    settings.CTF_LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300

    client.post(reverse("ctf:ctf_login"), {"username": standard_user.username, "password": "wrong"})
    client.post(reverse("ctf:ctf_login"), {"username": standard_user.username, "password": "wrong"})
    response = client.post(
        reverse("ctf:ctf_login"),
        {"username": standard_user.username, "password": "wrong"},
    )

    assert response.status_code == 429
    assert response["Retry-After"] == "300"


def test_first_password_change_rejects_bootstrap_reuse(client, ctf_event_active, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event_active.id, count=1)[0]
    client.post(
        reverse("ctf:ctf_login"),
        {"username": participant.user.username, "password": TEST_CTF_BOOTSTRAP_PASSWORD},
    )

    response = client.post(
        reverse("ctf:ctf_change_password"),
        {
            "old_password": TEST_CTF_BOOTSTRAP_PASSWORD,
            "new_password1": TEST_CTF_BOOTSTRAP_PASSWORD,
            "new_password2": TEST_CTF_BOOTSTRAP_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert "Choose a password different from the event bootstrap password" in response.content.decode()
    assert get_user_profile(participant.user).must_change_password is True


def test_organizer_can_render_and_generate_participant_batch(
    authenticated_organizer_client,
    ctf_event,
    monkeypatch,
):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    url = reverse("ctf:admin_participant_batch", kwargs={"event_id": ctf_event.id})

    get_response = authenticated_organizer_client.get(url)
    post_response = authenticated_organizer_client.post(url, {"count": 2})

    assert get_response.status_code == 200
    assert post_response.status_code == 302
    assert ctf_event.participants.filter(user__profile__is_ctf_account=True).count() == 2


def test_organizer_can_rename_and_attach_delivery_email(authenticated_organizer_client, ctf_event, monkeypatch):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event.id, count=1)[0]

    rename_response = authenticated_organizer_client.post(
        reverse("ctf:admin_participant_rename", kwargs={"participant_id": participant.id}),
        {"username": "range-renamed-seat"},
    )
    email_response = authenticated_organizer_client.post(
        reverse("ctf:admin_participant_email", kwargs={"participant_id": participant.id}),
        {"email": "Delivery@Example.test"},
    )

    participant.refresh_from_db()
    participant.user.refresh_from_db()
    assert rename_response.status_code == 302
    assert email_response.status_code == 302
    assert participant.user.username == "range-renamed-seat"
    assert participant.email == "delivery@example.test"


def test_organizer_participant_detail_reveals_current_bootstrap_password(
    authenticated_organizer_client,
    ctf_event,
    monkeypatch,
):
    monkeypatch.setattr("ctf.services.participant.accounts.request_event_provisioning", lambda *_a, **_kw: None)
    participant = create_participant_accounts(ctf_event.id, count=1)[0]

    response = authenticated_organizer_client.get(
        reverse("ctf:admin_participant_detail", kwargs={"participant_id": participant.id})
    )

    assert response.status_code == 200
    assert TEST_CTF_BOOTSTRAP_PASSWORD in response.content.decode()
