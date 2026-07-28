"""Per-protocol resolve-and-mint building blocks for the Guacamole session service.

Split out of :mod:`mission_control.guacamole_session` (issue #991) so that module
stays under Sonar S104's 500-line cap, mirroring the prior
``views/_guacamole`` + ``views/_guacamole_builders`` split. These are the
worker-side "resolve the sanctioned ``engine.services`` connection projection,
adapt it into the existing ``mission_control.guacamole`` request dataclass, then
mint the signed URL" helpers that the bootstrap worker runs off the request
thread (#929); the session module keeps the HTTP-neutral entry point, the
closed access-kind dispatch, and the enqueue glue.

All resolution/generation failures are raised as
:class:`~mission_control.guacamole_bootstrap.BootstrapFailure` (a safe message +
HTTP status code); the bootstrap worker persists them and the status endpoint
surfaces them. There is no presentation coupling here — no ``JsonResponse``, no
response-body re-parsing, and only the ADR-001-R4 allowlisted ``engine.services``
symbols are consumed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from django.conf import settings as django_settings

from mission_control.guacamole_bootstrap import BootstrapFailure
from shared.errors import classify_user_message
from shared.log_sanitize import safe_log_fingerprint, safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

_GUAC_AUTH_NOT_CONFIGURED = "Guacamole JSON auth is not configured"
_GUACAMOLE_BASE_PATH = "/guacamole"
_INTERNAL_SERVER_ERROR = "Internal server error"

# ``(signing_secret, browser_base_url, server_to_server_api_url)`` — the browser
# base URL is user-facing while the API URL is the internal token-mint endpoint.
GuacamoleSettings = tuple[str, str, str | None]


class _SSHConn(Protocol):
    """Structural type for an ``engine.services.SSHConnection``-like value.

    ``mission_control`` reads only the handful of attributes below from the
    sanctioned public Engine projection; this is not a second connection schema.
    """

    host: str
    port: int
    username: str
    private_key: str


# ---------------------------------------------------------------------------
# Session identity and configuration binding
# ---------------------------------------------------------------------------


def guacamole_identity(user: User) -> str:
    """Return the Guacamole JSON-auth session identity for ``user``.

    The Guacamole session username is an identity label, not an email. Platform
    (OIDC) users carry an email and use it, but isolated temporary CTF accounts
    (issue #1206) are created with a blank ``email`` and a unique ``range-<hex>``
    username. Passing the blank email to Guacamole is rejected with
    ``400 "The username must not be blank."``, so fall back to the account's
    unique username, which is also a correct per-user-isolated session identity.
    """
    return user.email or user.get_username()


def _guac_settings(service_name: str) -> GuacamoleSettings:
    """Bind Guacamole runtime configuration or raise a neutral 503 failure.

    Runs synchronously on the request thread so a missing signing secret fails
    closed before any bootstrap is enqueued (matching the prior view behaviour).
    """
    guacamole_signing_secret = getattr(django_settings, "GUACAMOLE_JSON_AUTH_SECRET", "")
    if not guacamole_signing_secret:
        logger.error(_GUAC_AUTH_NOT_CONFIGURED)
        raise BootstrapFailure(f"{service_name} service not configured", status_code=503)
    base_url = getattr(django_settings, "GUACAMOLE_BASE_URL", _GUACAMOLE_BASE_PATH)
    api_url = getattr(django_settings, "GUACAMOLE_API_BASE_URL", None)
    return guacamole_signing_secret, base_url, api_url


# ---------------------------------------------------------------------------
# RDP
# ---------------------------------------------------------------------------


_SFTP_ROOT_BY_OS: dict[str, str] = {
    "kali": "/home/kali",
    "ubuntu": "/home/ubuntu",
    # SFTP paths use forward slashes even on Windows.
    "windows": "/C:/Users/Administrator/Downloads",
}


def _sftp_root_for_os(os_type: str | None) -> str | None:
    """Return Guacamole SFTP root path for the given OS type, or None."""
    if os_type is None:
        return None
    return _SFTP_ROOT_BY_OS.get(os_type)


def _rdp_security_for_os(os_type: str | None) -> str:
    """Return Guacamole's RDP security mode for the given OS type.

    Every target negotiates: the mode is left to the RDP handshake rather than
    pinned per OS.

    Issue #1801 pinned Kali to ``tls`` on the assumption that xrdp only speaks
    TLS. That is not true of the range's Kali guest, and pinning breaks it: an
    X.224 negotiation probe against a live range host shows the server answering
    ``RDP_NEG_RSP`` with ``PROTOCOL_RDP`` (0) for *every* request — including
    requests for TLS, HYBRID/NLA, and RDSTLS. Pinning ``tls`` therefore makes
    guacd demand a protocol the guest never selects, and the session dies with
    "Security negotiation failed (wrong security type?)" after the tunnel and
    Guacamole authentication have both succeeded (issue #987).

    ``any`` lets the handshake settle it, so a guest that offers only legacy RDP
    security and a guest that offers TLS both connect without a per-image
    allowlist here.
    """
    return "any"


def _resolve_rdp_conn(user: User, instance_uuid: str) -> dict[str, Any]:
    """Resolve the RDP connection info or raise ``BootstrapFailure``."""
    from engine.services import get_rdp_connection_info

    try:
        return get_rdp_connection_info(user, instance_uuid)
    except ValueError as e:
        logger.exception(
            "RDP connection lookup failed: user=%s instance_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure(
            classify_user_message(str(e), default="RDP connection unavailable"), status_code=400
        ) from e


def _generate_rdp_url(
    *,
    username: str,
    conn_info: dict[str, Any],
    guacamole_signing_secret: str,
    guacamole_base_url: str,
    guacamole_api_url: str | None,
) -> str:
    """Generate the Guacamole RDP URL or raise ``BootstrapFailure``."""
    from mission_control.guacamole import GuacRDPUrlRequest, create_guacamole_rdp_url

    os_type = conn_info.get("os_type")
    sftp_root_directory = _sftp_root_for_os(os_type)
    try:
        return create_guacamole_rdp_url(
            GuacRDPUrlRequest(
                base_url=guacamole_base_url,
                secret_key=guacamole_signing_secret,
                username=username,
                connection_name=conn_info["connection_name"],
                hostname=conn_info["private_ip"],
                expires_minutes=5,
                rdp_username=conn_info.get("rdp_username"),
                rdp_password=conn_info.get("rdp_password"),
                api_base_url=guacamole_api_url,
                sftp_root_directory=sftp_root_directory,
                sftp_private_key=conn_info.get("ssh_key"),
                security=_rdp_security_for_os(os_type),
            )
        )
    except ValueError as e:
        logger.exception("Failed to generate Guacamole URL")
        raise BootstrapFailure("Failed to generate RDP URL", status_code=500) from e


def _build_rdp_url(*, user: User, instance_uuid: str, guac_settings: GuacamoleSettings) -> str:
    """Resolve RDP credentials and build the signed URL — runs in the worker.

    Credential resolution (the Secrets Manager fetch) happens here, inside the
    bootstrap worker, not on the request thread (#929).
    """
    conn_info = _resolve_rdp_conn(user, instance_uuid)
    guacamole_signing_secret, guacamole_base_url, guacamole_api_url = guac_settings
    # ``conn_info`` carries RDP credentials, so only non-secret metadata is
    # logged. ``os_type`` is read from the credential-bearing dict, so CodeQL
    # taints it regardless of naming; it goes through ``safe_log_fingerprint``
    # (a true ``py/clear-text-logging-sensitive-data`` taint-break). The
    # user/instance correlation IDs go through ``safe_log_value``.
    rdp_os = str(conn_info.get("os_type") or "unknown")
    file_transfer_available = "yes" if conn_info.get("ssh_key") else "no"
    logger.info(
        "Guac RDP request: user=%s instance_uuid=%s os=%s file_transfer_available=%s",
        safe_log_value(user.email),
        safe_log_value(instance_uuid),
        safe_log_fingerprint(rdp_os),
        file_transfer_available,
    )
    return _generate_rdp_url(
        username=guacamole_identity(user),
        conn_info=conn_info,
        guacamole_signing_secret=guacamole_signing_secret,
        guacamole_base_url=guacamole_base_url,
        guacamole_api_url=guacamole_api_url,
    )


# ---------------------------------------------------------------------------
# NGFW SSH
# ---------------------------------------------------------------------------


def _resolve_ngfw_ssh(user: User, app_id: str) -> _SSHConn:
    """Look up the NGFW SSH connection details or raise ``BootstrapFailure``."""
    from engine.services import connect_ngfw_terminal

    try:
        return connect_ngfw_terminal(user, app_id)
    except ValueError as e:
        logger.exception(
            "NGFW SSH access denied (ValueError): user=%s ngfw_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(app_id),
        )
        raise BootstrapFailure(classify_user_message(str(e), default="NGFW SSH unavailable"), status_code=400) from e
    except PermissionError as e:
        logger.exception(
            "NGFW SSH access denied (PermissionError): user=%s ngfw_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(app_id),
        )
        raise BootstrapFailure("Permission denied", status_code=400) from e
    except Exception as e:
        logger.exception(
            "Unexpected error getting NGFW SSH connection: user=%s ngfw_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(app_id),
        )
        raise BootstrapFailure(_INTERNAL_SERVER_ERROR, status_code=500) from e


def _generate_ngfw_ssh_url(
    *,
    username: str,
    app_id: str,
    ssh_conn: _SSHConn,
    guacamole_signing_secret: str,
    guacamole_base_url: str,
    guacamole_api_url: str | None,
) -> str:
    """Generate the Guacamole NGFW SSH URL or raise ``BootstrapFailure``."""
    from mission_control.guacamole import GuacSSHUrlRequest, create_guacamole_ssh_url

    try:
        return create_guacamole_ssh_url(
            GuacSSHUrlRequest(
                base_url=guacamole_base_url,
                secret_key=guacamole_signing_secret,
                username=username,
                connection_name=f"ngfw-{app_id}",
                hostname=ssh_conn.host,
                port=ssh_conn.port,
                ssh_username=ssh_conn.username,
                ssh_private_key=ssh_conn.private_key,
                expires_minutes=5,
                api_base_url=guacamole_api_url,
            )
        )
    except ValueError as e:
        logger.exception(
            "Failed to generate NGFW SSH URL: ngfw_uuid=%s",
            safe_log_value(app_id),
        )
        raise BootstrapFailure("Failed to generate SSH URL", status_code=500) from e
    except Exception as e:
        logger.exception(
            "Unexpected error generating NGFW SSH URL: ngfw_uuid=%s",
            safe_log_value(app_id),
        )
        raise BootstrapFailure(_INTERNAL_SERVER_ERROR, status_code=500) from e


def _build_ngfw_ssh_url(*, user: User, app_id: str, guac_settings: GuacamoleSettings) -> str:
    """Resolve NGFW SSH credentials and build the signed URL — runs in the worker.

    The ownership check and Secrets Manager fetch happen here, off the request
    thread (#929).
    """
    ssh_conn = _resolve_ngfw_ssh(user, app_id)
    guacamole_signing_secret, guacamole_base_url, guacamole_api_url = guac_settings
    return _generate_ngfw_ssh_url(
        username=guacamole_identity(user),
        app_id=app_id,
        ssh_conn=ssh_conn,
        guacamole_signing_secret=guacamole_signing_secret,
        guacamole_base_url=guacamole_base_url,
        guacamole_api_url=guacamole_api_url,
    )


# ---------------------------------------------------------------------------
# Range SSH
# ---------------------------------------------------------------------------


def _resolve_range_ssh(user: User, instance_uuid: str) -> dict[str, Any]:
    """Look up the range SSH connection info or raise ``BootstrapFailure``."""
    from engine.services import get_ssh_connection_info

    try:
        return get_ssh_connection_info(user, instance_uuid)
    except ValueError as e:
        logger.exception(
            "Range SSH access denied (ValueError): user=%s instance_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure(classify_user_message(str(e), default="Range SSH unavailable"), status_code=400) from e
    except PermissionError as e:
        logger.exception(
            "Range SSH access denied (PermissionError): user=%s instance_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure("Permission denied", status_code=400) from e
    except Exception as e:
        logger.exception(
            "Unexpected error getting range SSH connection: user=%s instance_uuid=%s",
            safe_log_value(user.email),
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure(_INTERNAL_SERVER_ERROR, status_code=500) from e


def _generate_range_ssh_url(
    *,
    username: str,
    instance_uuid: str,
    ssh_info: dict[str, Any],
    guacamole_signing_secret: str,
    guacamole_base_url: str,
    guacamole_api_url: str | None,
) -> str:
    """Generate the Guacamole range SSH URL or raise ``BootstrapFailure``."""
    from mission_control.guacamole import GuacSSHUrlRequest, create_guacamole_ssh_url

    try:
        return create_guacamole_ssh_url(
            GuacSSHUrlRequest(
                base_url=guacamole_base_url,
                secret_key=guacamole_signing_secret,
                username=username,
                connection_name=ssh_info["connection_name"],
                hostname=ssh_info["host"],
                port=ssh_info["port"],
                ssh_username=ssh_info["username"],
                ssh_private_key=ssh_info["private_key"],
                expires_minutes=5,
                api_base_url=guacamole_api_url,
            )
        )
    except ValueError as e:
        logger.exception(
            "Failed to generate range SSH URL: instance_uuid=%s",
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure("Failed to generate SSH URL", status_code=500) from e
    except Exception as e:
        logger.exception(
            "Unexpected error generating range SSH URL: instance_uuid=%s",
            safe_log_value(instance_uuid),
        )
        raise BootstrapFailure(_INTERNAL_SERVER_ERROR, status_code=500) from e


def _build_range_ssh_url(*, user: User, instance_uuid: str, guac_settings: GuacamoleSettings) -> str:
    """Resolve range SSH credentials and build the signed URL — runs in the worker.

    Credential resolution (the Secrets Manager fetch) happens here, off the
    request thread (#929).
    """
    ssh_info = _resolve_range_ssh(user, instance_uuid)
    guacamole_signing_secret, guacamole_base_url, guacamole_api_url = guac_settings
    # ``ssh_info`` carries the SSH private key. Only non-secret metadata is
    # logged: the host IP and cloud provider name. Both are read from the
    # credential-bearing dict, so CodeQL taints them regardless of naming; they
    # go through ``safe_log_fingerprint`` (a true taint-break) and stay
    # correlatable across log lines within the process. The user/instance
    # correlation IDs go through ``safe_log_value``.
    logger.info(
        "Guacamole SSH bootstrap queued for range instance: user=%s instance_uuid=%s host=%s provider=%s",
        safe_log_value(user.email),
        safe_log_value(instance_uuid),
        safe_log_fingerprint(ssh_info["host"]),
        safe_log_fingerprint(ssh_info.get("cloud_provider") or "unknown"),
    )
    return _generate_range_ssh_url(
        username=guacamole_identity(user),
        instance_uuid=instance_uuid,
        ssh_info=ssh_info,
        guacamole_signing_secret=guacamole_signing_secret,
        guacamole_base_url=guacamole_base_url,
        guacamole_api_url=guacamole_api_url,
    )
