"""Tests for cms.services query helpers.

Covers get_range_target_instances, which selects the instances shown on the
CTF participant range page. Explicit scenario ``participant_access`` bindings
are authoritative: POLARIS exposes the Kali workstation, not the DC. See #1465:
a single-seat purple-team lab (TechVault) provisions only an attacker-tagged
seat host, and the page must still show it.

The selector reads the user's ready range from the engine, so these exercise
the real database rather than mocking the first-party query seam (ADR-019-R1):
each case seeds an engine ``Range`` and asserts what the selector returns.
"""

import pytest
from django.contrib.auth import get_user_model

from cms.services import get_range_target_instances

# Opaque #1325 workspace scope binding (ADR-046-R3). These suites do not
# exercise tenancy; a fixed scalar stands in for the value the CMS launch
# facade resolves in production.
_WORKSPACE_ID = 1

User = get_user_model()

_ATTACKER = {
    "name": "techvault",
    "role": "attacker",
    "os_type": "kali",
    "private_ip": "10.1.2.22",
    "uuid": "aaaa",
}
_POLARIS_KALI = {
    **_ATTACKER,
    "name": "kali",
    "participant_access_channels": ["ssh", "rdp"],
}
_AWS_POLARIS_KALI = {
    **_ATTACKER,
    "name": "kali",
    "cloud_provider": "aws",
    "participant_access_channels": None,
}
_DC = {
    "name": "dc01",
    "role": "dc",
    "os_type": "windows",
    "private_ip": "10.1.2.30",
    "uuid": "bbbb",
}


@pytest.fixture
def user(db):
    return User.objects.create_user(username="queries@example.com", email="queries@example.com")


def _ready_range(user, provisioned_instances):
    """Seed the user's ready engine Range with the given provisioned instances."""
    from engine.models import Range as EngineRange

    return EngineRange.objects.create(
        workspace_id=_WORKSPACE_ID,
        user=user,
        status=EngineRange.Status.READY,
        provisioned_instances=provisioned_instances,
    )


class TestGetRangeTargetInstances:
    """Behavior of the CTF range-page instance selector."""

    def test_explicit_participant_access_returns_declared_target(self, user):
        """POLARIS-style range: the declared Kali workstation is the user target."""
        _ready_range(user, [_POLARIS_KALI, _DC])
        assert get_range_target_instances(user.id) == [_POLARIS_KALI]

    def test_aws_open_access_returns_attacker_seat(self, user):
        """AWS POLARIS state exposes the Kali seat when no closed binding exists."""
        _ready_range(user, [_AWS_POLARIS_KALI, _DC])
        assert get_range_target_instances(user.id) == [_AWS_POLARIS_KALI]

    def test_legacy_multi_node_hides_attacker_and_shows_targets(self, user):
        """Legacy rows without access channels keep the non-attacker heuristic."""
        _ready_range(user, [_ATTACKER, _DC])
        assert get_range_target_instances(user.id) == [_DC]

    def test_single_seat_lab_returns_attacker_seat(self, user):
        """TechVault-style range: the sole attacker-tagged seat is returned (#1465)."""
        _ready_range(user, [_ATTACKER])
        assert get_range_target_instances(user.id) == [_ATTACKER]

    def test_no_ready_range_returns_empty(self, user):
        """No ready range / no instances yields an empty list."""
        assert get_range_target_instances(user.id) == []
