"""PostgreSQL arbitration of simultaneous personal-token rotation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from django.db import close_old_connections
from django.utils import timezone

from management.personal_credentials import issue_personal_token, rotate_personal_token
from management.services import principal_for_user
from shared.api_tokens.models import ApiToken
from shared.authorization import AuthorizationDecision, CredentialCeiling, DecisionKind, TargetRef, port
from shared.credentials import CredentialContext
from shared.identity_scope import ResourceScope


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_rotators_cannot_leave_two_live_replacements(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="rotating-owner")
    principal = principal_for_user(user)
    actor = CredentialContext(
        principal, "session", principal.uuid, CredentialCeiling(frozenset({"installation.read_audit"})), frozenset()
    )
    provider = SimpleNamespace(check=lambda request: AuthorizationDecision(DecisionKind.ALLOWED, "policy_allowed"))
    monkeypatch.setattr(port, "_provider_factory", lambda: provider)
    command = {
        "actor": actor,
        "user": user,
        "name": "rotation",
        "scopes": ["authorization:installation.read_audit"],
        "expires_at": timezone.now() + timedelta(hours=1),
        "target": TargetRef("installation"),
        "scope": ResourceScope("installation"),
    }
    previous, raw = issue_personal_token(**command)
    barrier = Barrier(2)

    def rotate():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return rotate_personal_token(previous.credential_uuid, **command)[0].credential_uuid
            except ValueError:
                return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert ApiToken.authenticate(raw) is None
    assert ApiToken.objects.filter(created_by=user, revoked_at__isnull=True).count() == 1
