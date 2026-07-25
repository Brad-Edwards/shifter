"""HTTP-level tests for the rehomed ``/api/v1/audit/`` read endpoint (#1374).

Drives the real DRF surface (not the permission classes in isolation) so the
whole authentication + authorization + response-shape chain is exercised
end-to-end. Authorization is a compound gate restoring the pre-#1374
risk-register semantics under an audit-owned name: a session principal must
be BOTH a member of a configured Cognito group AND staff/superuser. Everything
else -- wrong group, no group config at all, non-staff, anonymous, any API
token -- is denied, and every denial emits an ``ACCESS_DENIED`` audit row.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from management.services import get_user_profile
from shared.api_tokens import scopes
from shared.api_tokens.models import ApiToken
from shared.audit import AuditAction, AuditActorType, AuditEntityType, AuditEvent, audit_log
from shared.models import AuditLog

pytestmark = pytest.mark.django_db

AUDIT_URL = "/api/v1/audit/"
ALLOWED_GROUPS = ["security"]


@pytest.fixture(autouse=True)
def audit_log_allowed_groups(settings):
    """Configure the Cognito-group allow-list for every test by default.

    Individual tests override this via ``settings.AUDIT_LOG_ALLOWED_COGNITO_GROUPS``
    to exercise the fail-closed-when-unconfigured path.
    """
    settings.AUDIT_LOG_ALLOWED_COGNITO_GROUPS = ALLOWED_GROUPS


def _grant_group(user, groups=None):
    """Persist Cognito groups on ``user``'s profile (the session-fallback path)."""
    profile = get_user_profile(user)
    profile.cognito_groups = list(groups if groups is not None else ALLOWED_GROUPS)
    profile.save(update_fields=["cognito_groups"])
    return profile


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def staff_user(django_user_model):
    """A staff session, granted the allowed Cognito group by default."""
    user = django_user_model.objects.create_user(
        username="staff@example.com",
        email="staff@example.com",
        password="pw",
        is_staff=True,
    )
    _grant_group(user)
    return user


@pytest.fixture
def plain_user(django_user_model):
    """A non-staff session, granted the allowed Cognito group by default.

    Granting the group isolates the staff check as the sole reason for
    denial in the "non-staff" tests below -- proving the compound gate still
    requires staff even when group membership alone would pass.
    """
    user = django_user_model.objects.create_user(
        username="plain@example.com",
        email="plain@example.com",
        password="pw",
    )
    _grant_group(user)
    return user


@pytest.fixture
def seeded_audit_row():
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.RANGE,
            entity_id=1,
            action=AuditAction.CREATE,
            actor_type=AuditActorType.SYSTEM,
            context="seed row",
        )
    )
    return AuditLog.objects.get(entity_type=AuditEntityType.RANGE, entity_id=1)


class TestStaffAccess:
    def test_staff_session_can_list(self, client, staff_user, seeded_audit_row):
        client.force_authenticate(user=staff_user)
        response = client.get(AUDIT_URL)
        assert response.status_code == 200
        ids = [row["id"] for row in response.json()["results"]]
        assert seeded_audit_row.id in ids

    def test_superuser_session_can_list(self, client, django_user_model, seeded_audit_row):
        superuser = django_user_model.objects.create_user(
            username="super@example.com",
            email="super@example.com",
            password="pw",
            is_superuser=True,
        )
        _grant_group(superuser)
        client.force_authenticate(user=superuser)
        assert client.get(AUDIT_URL).status_code == 200


class TestNonStaffDenied:
    def test_authenticated_non_staff_is_403(self, client, plain_user):
        client.force_authenticate(user=plain_user)
        assert client.get(AUDIT_URL).status_code == 403

    def test_anonymous_is_401_or_403(self, client):
        assert client.get(AUDIT_URL).status_code in (401, 403)

    def test_denial_emits_access_denied_audit_row(self, client, plain_user):
        client.force_authenticate(user=plain_user)
        client.get(AUDIT_URL)
        assert AuditLog.objects.filter(
            action=AuditAction.ACCESS_DENIED,
            context__icontains="Not a staff/superuser session",
        ).exists()


class TestCognitoGroupGate:
    """Compound authorization: staff/superuser AND an allowed Cognito group (#1374)."""

    def test_staff_not_in_group_is_403(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="staff-no-group@example.com",
            email="staff-no-group@example.com",
            password="pw",
            is_staff=True,
        )
        # No group granted -- profile.cognito_groups defaults empty.
        client.force_authenticate(user=user)
        assert client.get(AUDIT_URL).status_code == 403

    def test_staff_not_in_group_emits_access_denied_audit_row(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="staff-no-group2@example.com",
            email="staff-no-group2@example.com",
            password="pw",
            is_staff=True,
        )
        client.force_authenticate(user=user)
        client.get(AUDIT_URL)
        assert AuditLog.objects.filter(
            action=AuditAction.ACCESS_DENIED,
            context__icontains="Not in allowed Cognito group",
        ).exists()

    def test_unconfigured_group_list_denies_staff(self, client, staff_user, settings):
        # Fail-closed: an unconfigured allow-list denies everyone, staff
        # (and group membership on the profile) notwithstanding.
        settings.AUDIT_LOG_ALLOWED_COGNITO_GROUPS = []
        client.force_authenticate(user=staff_user)
        response = client.get(AUDIT_URL)
        assert response.status_code == 403

    def test_unconfigured_group_list_emits_access_denied_audit_row(self, client, staff_user, settings):
        settings.AUDIT_LOG_ALLOWED_COGNITO_GROUPS = []
        client.force_authenticate(user=staff_user)
        client.get(AUDIT_URL)
        assert AuditLog.objects.filter(
            action=AuditAction.ACCESS_DENIED,
            context__icontains="AUDIT_LOG_ALLOWED_COGNITO_GROUPS is not configured",
        ).exists()

    def test_session_groups_take_precedence_over_stale_profile(self, client, staff_user):
        # staff_user's profile carries the allowed group; a session that has
        # since captured a *different* group set must win over that stale
        # profile snapshot, so access is denied.
        client.force_authenticate(user=staff_user)
        session = client.session
        session["cognito_groups"] = ["other-group"]
        session.save()
        assert client.get(AUDIT_URL).status_code == 403

    def test_session_groups_can_grant_access_without_profile(self, client, django_user_model):
        # A staff user with no profile group grant is still admitted when the
        # session itself carries an allowed group (proves the session path is
        # live, not just the profile fallback).
        user = django_user_model.objects.create_user(
            username="staff-session-only@example.com",
            email="staff-session-only@example.com",
            password="pw",
            is_staff=True,
        )
        client.force_authenticate(user=user)
        session = client.session
        session["cognito_groups"] = ALLOWED_GROUPS
        session.save()
        assert client.get(AUDIT_URL).status_code == 200


class TestApiTokenRejected:
    def test_valid_platform_token_is_still_denied(self, client, staff_user):
        # The audit endpoint accepts no token scope at all (ADR-029): even a
        # token minted by a staff user in the allowed group must not read
        # audit rows.
        _, raw = ApiToken.create_token(name="t", created_by=staff_user, scopes=[scopes.MISSION_CONTROL_RANGE_READ])
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = client.get(AUDIT_URL)
        assert response.status_code == 403

    def test_token_denial_emits_access_denied_audit_row(self, client, staff_user):
        _, raw = ApiToken.create_token(name="t", created_by=staff_user, scopes=[scopes.MISSION_CONTROL_RANGE_READ])
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        client.get(AUDIT_URL)
        assert AuditLog.objects.filter(
            action=AuditAction.ACCESS_DENIED,
            context__icontains="API token rejected for audit reads",
        ).exists()


class TestResponseShape:
    def test_response_carries_historical_retired_entity_types_as_plain_strings(self, client, staff_user):
        """Historical rows retain retired ``"risk"``/``"comment"`` values (#1374).

        The read serializer must surface them as-is (a plain string field, not
        a closed enum), proving the published contract does not lie about what
        old rows actually contain.
        """
        AuditLog.objects.create(
            entity_type="risk",
            entity_id=99,
            action="create",
            actor_type=AuditActorType.SYSTEM,
        )
        client.force_authenticate(user=staff_user)
        response = client.get(AUDIT_URL, {"entity_type": "risk"})
        assert response.status_code == 200
        results = response.json()["results"]
        assert any(row["entity_type"] == "risk" for row in results)

    def test_entity_type_field_is_plain_charfield_not_choicefield(self):
        from rest_framework import serializers

        from shared.api.audit import AuditLogSerializer

        field = AuditLogSerializer().fields["entity_type"]
        assert isinstance(field, serializers.CharField)
        assert not isinstance(field, serializers.ChoiceField)


@pytest.mark.django_db
class TestQueryFilters:
    """The endpoint's query-param filters are part of the behavior #1374 preserves.

    Each filter selects the seeded row and excludes a deliberately non-matching
    one, so a filter silently dropped during the rehome fails here rather than
    quietly returning the whole audit trail.
    """

    @pytest.fixture
    def two_rows(self):
        """One RANGE/CREATE/SYSTEM row and one distinct SCENARIO/DELETE/USER row."""
        audit_log(
            AuditEvent(
                entity_type=AuditEntityType.RANGE,
                entity_id=41,
                action=AuditAction.CREATE,
                actor_type=AuditActorType.SYSTEM,
                actor_id=7,
                context="wanted",
                request_id="req-wanted",
            )
        )
        audit_log(
            AuditEvent(
                entity_type=AuditEntityType.SCENARIO,
                entity_id=99,
                action=AuditAction.DELETE,
                actor_type=AuditActorType.USER,
                actor_id=8,
                context="unwanted",
                request_id="req-unwanted",
            )
        )
        return (
            AuditLog.objects.get(request_id="req-wanted"),
            AuditLog.objects.get(request_id="req-unwanted"),
        )

    @pytest.mark.parametrize(
        "param,value",
        [
            ("entity_type", AuditEntityType.RANGE),
            ("entity_id", "41"),
            ("action", AuditAction.CREATE),
            ("actor_type", AuditActorType.SYSTEM),
            ("actor_id", "7"),
            ("request_id", "req-wanted"),
        ],
    )
    def test_filter_selects_only_the_matching_row(self, client, staff_user, two_rows, param, value):
        wanted, unwanted = two_rows
        client.force_authenticate(user=staff_user)
        response = client.get(AUDIT_URL, {param: value})
        assert response.status_code == 200
        ids = [row["id"] for row in response.json()["results"]]
        assert wanted.id in ids, f"{param} filter dropped the matching row"
        assert unwanted.id not in ids, f"{param} filter did not exclude the non-matching row"

    def test_date_range_bounds_the_result_set(self, client, staff_user, two_rows):
        """``from_date`` / ``to_date`` bound on ``timestamp``; a window before the
        seeded rows must return nothing, and one around them must return them."""
        wanted, _unwanted = two_rows
        client.force_authenticate(user=staff_user)

        before = client.get(AUDIT_URL, {"to_date": "2000-01-01"})
        assert before.status_code == 200
        assert before.json()["results"] == []

        around = client.get(AUDIT_URL, {"from_date": "2000-01-01"})
        assert around.status_code == 200
        assert wanted.id in [row["id"] for row in around.json()["results"]]
