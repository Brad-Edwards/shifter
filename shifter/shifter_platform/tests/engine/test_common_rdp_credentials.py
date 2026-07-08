"""Tests for RDP credential resolution (engine.services._common).

Covers the seat-user resolution added for TechVault (#1465): a non-DC guest
RDPs in as its recorded ssh_username when present, else the os_type default;
a domain controller keeps its domain-admin login.
"""

from unittest.mock import patch

from engine.services import _common


class TestResolveRdpCredentials:
    """_resolve_rdp_credentials username/password selection."""

    def test_prefers_recorded_ssh_username_over_os_default(self):
        """TechVault: os_type kali but seat user ubuntu -> RDP as ubuntu (#1465)."""
        inst = {"os_type": "kali", "role": "attacker", "ssh_username": "ubuntu"}
        with patch.object(_common, "_resolve_non_dc_rdp_password", return_value="pw"):
            username, password = _common._resolve_rdp_credentials(inst)
        assert username == "ubuntu"
        assert password == "pw"

    def test_falls_back_to_os_default_when_no_ssh_username(self):
        """Standard kali host with no recorded seat user -> os_type default."""
        inst = {"os_type": "kali", "role": "attacker"}
        with patch.object(_common, "_resolve_non_dc_rdp_password", return_value="pw"):
            username, _ = _common._resolve_rdp_credentials(inst)
        assert username == "kali"

    def test_domain_controller_keeps_domain_admin(self):
        """A DC RDPs as the domain admin regardless of any ssh_username."""
        inst = {"os_type": "windows", "role": "dc", "ssh_username": "ignore-me"}
        with patch.object(_common, "_resolve_dc_password", return_value="dcpw"):
            username, password = _common._resolve_rdp_credentials(inst)
        assert username == "Administrator"
        assert password == "dcpw"
