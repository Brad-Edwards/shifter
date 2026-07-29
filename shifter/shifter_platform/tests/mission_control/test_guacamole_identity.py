"""Guacamole session identity for isolated CTF accounts (issue #1740).

Isolated temporary CTF participant accounts (issue #1206) are created with a
blank ``email``. The Guacamole JSON-auth username must fall back to the unique
``range-<hex>`` username, otherwise Guacamole rejects the token exchange with
``400 "The username must not be blank."`` and the participant cannot reach their
range box.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

from mission_control._guacamole_session_builders import _build_rdp_url, guacamole_identity
from mission_control.guacamole import RDPConnectionParams, create_rdp_connection_params

User = get_user_model()

_CONN_INFO = {
    "connection_name": "dc01",
    "private_ip": "10.1.2.56",
    "host": "10.1.2.56",
    "os_type": "windows",
    "rdp_username": "Administrator",
    "rdp_password": "secret-password",
    "ssh_key": None,
}
_GUAC_SETTINGS = ("signing-secret", "https://example/guacamole", None)


def test_guacamole_identity_prefers_email_when_present():
    user = User(username="range-abcd1234", email="player@example.com")
    assert guacamole_identity(user) == "player@example.com"


def test_guacamole_identity_falls_back_to_username_when_email_blank():
    # Isolated CTF accounts carry a blank email; the unique username is the identity.
    user = User(username="range-abcd1234", email="")
    assert guacamole_identity(user) == "range-abcd1234"


def test_rdp_url_build_uses_nonblank_identity_for_email_less_account(monkeypatch):
    """The RDP flow must hand Guacamole a non-blank username for a blank-email account.

    Reverting the fix to ``user.email`` makes the captured username blank and
    this assertion fails, so the test guards the enforcement (issue #1740).
    """
    user = User(username="range-abcd1234", email="")
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "mission_control._guacamole_session_builders._resolve_rdp_conn",
        lambda _user, _instance_uuid: dict(_CONN_INFO),
    )

    def _fake_create(req):
        captured["username"] = req.username
        return "https://example/guacamole/#/client/abc?token=t"

    monkeypatch.setattr("mission_control.guacamole.create_guacamole_rdp_url", _fake_create)

    url = _build_rdp_url(user=user, instance_uuid="inst-uuid", guac_settings=_GUAC_SETTINGS)

    assert captured["username"] == "range-abcd1234"
    assert captured["username"], "Guacamole username must never be blank"
    assert url.startswith("https://example/guacamole/#/client/")


def test_rdp_url_build_leaves_kali_security_on_negotiate(monkeypatch):
    """Kali must negotiate, not pin TLS.

    The range's Kali guest answers every X.224 negotiation request — TLS,
    HYBRID/NLA, RDSTLS — with PROTOCOL_RDP, so pinning ``tls`` (the old #1801
    behaviour) made guacd demand a protocol the guest never selects and the
    session failed with "Security negotiation failed" after Guacamole
    authentication had already succeeded (issue #987).
    """
    user = User(username="range-abcd1234", email="player@example.com")
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "mission_control._guacamole_session_builders._resolve_rdp_conn",
        lambda _user, _instance_uuid: {**_CONN_INFO, "os_type": "kali"},
    )

    def _fake_create(req):
        captured["security"] = req.security
        return "https://example/guacamole/#/client/abc?token=t"

    monkeypatch.setattr("mission_control.guacamole.create_guacamole_rdp_url", _fake_create)

    _build_rdp_url(user=user, instance_uuid="inst-uuid", guac_settings=_GUAC_SETTINGS)

    assert captured["security"] == "any"


def test_rdp_url_build_leaves_windows_security_on_negotiate(monkeypatch):
    """Windows RDP keeps Guacamole's default negotiate security mode."""
    user = User(username="range-abcd1234", email="player@example.com")
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "mission_control._guacamole_session_builders._resolve_rdp_conn",
        lambda _user, _instance_uuid: dict(_CONN_INFO),
    )

    def _fake_create(req):
        captured["security"] = req.security
        return "https://example/guacamole/#/client/abc?token=t"

    monkeypatch.setattr("mission_control.guacamole.create_guacamole_rdp_url", _fake_create)

    _build_rdp_url(user=user, instance_uuid="inst-uuid", guac_settings=_GUAC_SETTINGS)

    assert captured["security"] == "any"


def test_rdp_url_build_disables_sftp_when_endpoint_declares_it_unavailable(monkeypatch):
    user = User(username="range-abcd1234", email="")
    captured: dict[str, bool] = {}

    monkeypatch.setattr(
        "mission_control._guacamole_session_builders._resolve_rdp_conn",
        lambda _user, _instance_uuid: {**_CONN_INFO, "os_type": "kali", "sftp_enabled": False},
    )

    def _fake_create(req):
        captured["sftp_enabled"] = req.sftp_enabled
        return "https://example/guacamole/#/client/abc?token=t"

    monkeypatch.setattr("mission_control.guacamole.create_guacamole_rdp_url", _fake_create)

    _build_rdp_url(user=user, instance_uuid="inst-uuid", guac_settings=_GUAC_SETTINGS)

    assert captured["sftp_enabled"] is False


def test_rdp_params_keep_desktop_credentials_without_sftp():
    params = create_rdp_connection_params(
        RDPConnectionParams(
            hostname="10.50.2.19",
            username="desktop-user",
            password="desktop-password",
            sftp_enabled=False,
        )
    )

    assert params["username"] == "desktop-user"
    assert params["password"] == "desktop-password"
    assert "enable-sftp" not in params
    assert "sftp-password" not in params
