"""Tests for cms.services query helpers.

Covers get_range_target_instances, which selects the instances shown on the
CTF participant range page. See #1465: a single-seat purple-team lab (TechVault)
provisions only an attacker-tagged seat host, and the page must still show it.
"""

from unittest.mock import patch

from cms.services import get_range_target_instances

_ATTACKER = {
    "name": "techvault",
    "role": "attacker",
    "os_type": "kali",
    "private_ip": "10.1.2.22",
    "uuid": "aaaa",
}
_DC = {
    "name": "dc01",
    "role": "dc",
    "os_type": "windows",
    "private_ip": "10.1.2.30",
    "uuid": "bbbb",
}


def _ready_instances(instances):
    # get_range_target_instances imports get_user_ready_range_instances from
    # engine.services at call time, so patch it there.
    return patch("engine.services.get_user_ready_range_instances", return_value=instances)


class TestGetRangeTargetInstances:
    """Behavior of the CTF range-page instance selector."""

    def test_multi_node_hides_attacker_and_shows_targets(self):
        """POLARIS-style range: attacker workstation hidden, targets shown."""
        with _ready_instances([_ATTACKER, _DC]):
            assert get_range_target_instances(2) == [_DC]

    def test_single_seat_lab_returns_attacker_seat(self):
        """TechVault-style range: the sole attacker-tagged seat is returned (#1465)."""
        with _ready_instances([_ATTACKER]):
            assert get_range_target_instances(2) == [_ATTACKER]

    def test_no_ready_range_returns_empty(self):
        """No ready range / no instances yields an empty list."""
        with _ready_instances([]):
            assert get_range_target_instances(2) == []
