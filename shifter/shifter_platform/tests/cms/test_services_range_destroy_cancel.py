"""Behavior tests for CMS destroy_range / cancel_range services.

Drives the real services against a real provisioned range (cms RangeInstance +
matching engine Range/Request; engine ECS unconfigured so teardown is a no-op),
instead of patching ``RangeInstance.objects`` / ``get_range`` / the engine calls /
``audit_log``.
"""

import pytest
from django.contrib.auth import get_user_model

from cms import services
from cms.exceptions import CMSError
from cms.models import RangeInstance
from risk_register.models import AuditLog
from shared.enums import ResourceStatus
from tests.conftest import INVALID_RANGE_IDS, INVALID_USERS

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cms-destroy@example.com", email="cms-destroy@example.com")


def _reload(range_id):
    # Destroy/cancel soft-delete the row, so read it through the unfiltered manager.
    return RangeInstance.all_objects.get(range_id=range_id)


class TestDestroyRange:
    def test_sets_status_to_destroying_and_soft_deletes(self, user, provision_range):
        provision_range(user, range_id=42)
        assert services.destroy_range(user, 42) is None
        ri = _reload(42)
        assert ri.status == ResourceStatus.DESTROYING.value
        assert ri.deleted_at is not None

    def test_records_deprovision_audit(self, user, provision_range):
        provision_range(user, range_id=42)
        services.destroy_range(user, 42)
        assert AuditLog.objects.filter(
            entity_type=AuditLog.EntityType.RANGE, entity_id=42, action=AuditLog.Action.DEPROVISION
        ).exists()

    def test_raises_cms_error_when_range_not_found(self, user):
        with pytest.raises(CMSError, match="Range 999 not found"):
            services.destroy_range(user, 999)

    def test_raises_cms_error_when_not_owner(self, user, django_user_model, provision_range):
        other = django_user_model.objects.create_user(username="cms-d-other@e.com", email="cms-d-other@e.com")
        provision_range(other, range_id=77)
        with pytest.raises(CMSError, match="Range 77 not found"):
            services.destroy_range(user, 77)

    def test_requires_user_argument(self):
        with pytest.raises(TypeError):
            services.destroy_range(range_id=42)

    @pytest.mark.parametrize("invalid_user", INVALID_USERS)
    def test_raises_on_invalid_user(self, invalid_user):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            services.destroy_range(invalid_user, 42)

    def test_requires_range_id_argument(self, user):
        with pytest.raises(TypeError):
            services.destroy_range(user)

    @pytest.mark.parametrize("invalid_range_id", INVALID_RANGE_IDS)
    def test_raises_on_invalid_range_id(self, user, invalid_range_id):
        with pytest.raises((TypeError, ValueError)):
            services.destroy_range(user, invalid_range_id)


class TestCancelRange:
    def test_sets_status_to_destroyed(self, user, provision_range):
        provision_range(user, range_id=42)
        assert services.cancel_range(user, 42) is None
        assert _reload(42).status == ResourceStatus.DESTROYED.value

    def test_records_cancel_audit(self, user, provision_range):
        provision_range(user, range_id=42)
        services.cancel_range(user, 42)
        assert AuditLog.objects.filter(
            entity_type=AuditLog.EntityType.RANGE, entity_id=42, action=AuditLog.Action.CANCEL
        ).exists()

    def test_raises_cms_error_when_range_not_found(self, user):
        with pytest.raises(CMSError):
            services.cancel_range(user, 999)

    def test_raises_cms_error_when_not_owner(self, user, django_user_model, provision_range):
        other = django_user_model.objects.create_user(username="cms-c-other@e.com", email="cms-c-other@e.com")
        provision_range(other, range_id=88)
        with pytest.raises(CMSError):
            services.cancel_range(user, 88)

    def test_requires_user_argument(self):
        with pytest.raises(TypeError):
            services.cancel_range(range_id=42)

    @pytest.mark.parametrize("invalid_user", INVALID_USERS)
    def test_raises_on_invalid_user(self, invalid_user):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            services.cancel_range(invalid_user, 42)

    def test_requires_range_id_argument(self, user):
        with pytest.raises(TypeError):
            services.cancel_range(user)

    @pytest.mark.parametrize("invalid_range_id", INVALID_RANGE_IDS)
    def test_raises_on_invalid_range_id(self, user, invalid_range_id):
        with pytest.raises((TypeError, ValueError)):
            services.cancel_range(user, invalid_range_id)
