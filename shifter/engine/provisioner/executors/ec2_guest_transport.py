"""Strict host-management coordinates for native EC2 guest realization."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from executors.guest_ssh_executor import GuestSSHExecutor

_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def native_ec2_executor(instance: dict[str, Any], secret_reader) -> tuple[GuestSSHExecutor, str]:
    """Resolve only the trusted realizer's management projection, never participant keys.

    Native guests have no SSM role. Missing management identity must fail before
    credential lookup, without falling back to SSM or participant access fields.
    """
    target = str(instance.get("private_ip") or "")
    address = ipaddress.ip_address(target)
    username = instance.get("host_ssh_username")
    port = instance.get("host_ssh_port")
    host_key = instance.get("host_public_key")
    secret_ref = instance.get("host_ssh_key_secret_ref")
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or not any(address in network for network in _PRIVATE_NETWORKS)
        or not isinstance(username, str)
        or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", username)
        or type(port) is not int
        or not 1 <= port <= 65535
        or not isinstance(host_key, str)
        or not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: [^\r\n]+)?", host_key)
        or len(host_key) > 4096
        or not isinstance(secret_ref, str)
        or not 1 <= len(secret_ref) <= 2048
        or any(ord(char) < 32 for char in secret_ref)
    ):
        raise ValueError("Native EC2 management access is invalid")
    return GuestSSHExecutor(
        private_key=secret_reader(secret_ref),
        username=username,
        port=port,
        host_public_key=host_key,
        known_hosts_host=target,
    ), target
