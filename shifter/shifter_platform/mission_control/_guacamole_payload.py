"""JSON-auth payload construction and per-protocol connection parameters.

Split out of :mod:`mission_control.guacamole` (Sonar S104's 500-line cap) so
that module keeps the network-facing half -- the ``/api/tokens`` exchange with
its bounded readiness retry, and the signed-URL builders. Everything here is
pure: it builds the payload dict, signs and encrypts it per Guacamole's JSON
auth specification, and shapes the RDP/SSH connection parameter maps.

See: https://guacamole.apache.org/doc/gug/json-auth.html
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def create_guacamole_auth_payload(
    username: str,
    connections: dict[str, dict[str, Any]],
    expires_minutes: int = 5,
) -> dict[str, Any]:
    """Create the JSON payload for Guacamole JSON auth.

    Args:
        username: Username for the Guacamole session (typically user's email)
        connections: Dictionary of connection definitions
        expires_minutes: Minutes until the payload expires

    Returns:
        Dictionary payload ready for signing

    Example connection:
        {
            "rdp-connection": {
                "protocol": "rdp",
                "parameters": {
                    "hostname": "10.1.5.10",
                    "port": "3389",
                    "ignore-cert": "true",
                    "security": "any"
                }
            }
        }
    """
    expires_ms = int((time.time() + expires_minutes * 60) * 1000)

    return {
        "username": username,
        "expires": expires_ms,
        "connections": connections,
    }


def sign_and_encrypt_payload(payload: dict[str, Any], secret_key: str) -> str:
    """Sign and encrypt a Guacamole JSON auth payload.

    The process follows Guacamole's JSON auth specification:
    1. Convert payload to JSON bytes
    2. Create HMAC-SHA256 signature using secret key
    3. Prepend binary signature to JSON bytes
    4. Encrypt with AES-128-CBC using zero IV
    5. Base64 encode the result

    Args:
        payload: The JSON auth payload dictionary
        secret_key: Hex string key (64 characters / 256-bit preferred)

    Returns:
        Base64-encoded encrypted payload for use as 'data' parameter
    """
    # Convert secret key from hex string to bytes
    key_bytes = bytes.fromhex(secret_key)
    if len(key_bytes) not in {16, 24, 32}:
        raise ValueError("Secret key must be 32, 48, or 64 hex characters (128, 192, or 256 bits)")

    # Convert payload to JSON bytes
    json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # Create HMAC-SHA256 signature
    signature = hmac.new(key_bytes, json_bytes, hashlib.sha256).digest()

    # Prepend signature to JSON
    signed_data = signature + json_bytes

    # Pad to AES block size (16 bytes)
    block_size = 16
    padding_length = block_size - (len(signed_data) % block_size)
    padded_data = signed_data + bytes([padding_length]) * padding_length

    # Encrypt with AES-128-CBC using zero IV
    iv = b"\x00" * 16
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()

    # Base64 encode
    return base64.b64encode(encrypted_data).decode("utf-8")


@dataclass(frozen=True)
class RDPConnectionParams:
    """Inputs for ``create_rdp_connection_params``.

    Bundling avoids the function's long positional/keyword signature
    (Sonar python:S107) while preserving every field's semantics.
    """

    hostname: str
    port: int = 3389
    username: str | None = None
    password: str | None = None
    ignore_cert: bool = True
    security: str = "any"
    sftp_root_directory: str | None = None
    sftp_private_key: str | None = None
    sftp_enabled: bool = True


def create_rdp_connection_params(req: RDPConnectionParams) -> dict[str, str]:
    """Create RDP connection parameters for Guacamole.

    Args:
        req: Bundled RDP connection inputs (see ``RDPConnectionParams``).

    Returns:
        Dictionary of RDP parameters for Guacamole
    """
    hostname = req.hostname
    port = req.port
    username = req.username
    password = req.password
    sftp_root_directory = req.sftp_root_directory
    sftp_private_key = req.sftp_private_key

    params: dict[str, str] = {
        "hostname": hostname,
        "port": str(port),
        "ignore-cert": "true" if req.ignore_cert else "false",
        "security": req.security,
        "resize-method": "display-update",
        # Clipboard support
        "disable-copy": "false",
        "disable-paste": "false",
        # Performance optimizations - reduce bandwidth and server-side rendering load
        "color-depth": "16",
        "disable-audio": "true",
        "enable-wallpaper": "false",
        "enable-theming": "false",
        "enable-font-smoothing": "false",
        "enable-full-window-drag": "false",
        "enable-desktop-composition": "false",
        "enable-menu-animations": "false",
    }

    # SFTP file transfer - works reliably for both Windows and Linux (xrdp)
    # Uses SSH connection for file transfers via Guacamole menu (Ctrl+Alt+Shift)
    if req.sftp_enabled and username and (password or sftp_private_key):
        params["enable-sftp"] = "true"
        params["sftp-hostname"] = hostname
        params["sftp-port"] = "22"
        params["sftp-username"] = username
        # Prefer key-based auth (required for Windows OpenSSH)
        if sftp_private_key:
            params["sftp-private-key"] = sftp_private_key
        elif password:
            params["sftp-password"] = password
        if sftp_root_directory:
            params["sftp-root-directory"] = sftp_root_directory
            # sftp-directory is the upload destination for drag-and-drop transfers
            params["sftp-directory"] = sftp_root_directory

    if username:
        params["username"] = username
    if password:
        params["password"] = password

    return params


def create_ssh_connection_params(
    username: str,
    hostname: str,
    port: int = 22,
    ssh_private_key: str | None = None,
) -> dict[str, str]:
    """Create SSH connection parameters for Guacamole.

    Args:
        username: SSH username for login
        hostname: Target host IP or hostname
        port: SSH port (default 22)
        ssh_private_key: PEM-encoded private key for authentication

    Returns:
        Dictionary of SSH parameters for Guacamole
    """
    params: dict[str, str] = {
        "hostname": hostname,
        "port": str(port),
        "username": username,
        # Terminal settings
        "color-scheme": "green-black",
        "font-name": "monospace",
        "font-size": "12",
        # Clipboard support
        "enable-clipboard": "true",
    }

    if ssh_private_key:
        params["private-key"] = ssh_private_key

    return params
