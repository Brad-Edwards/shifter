"""Provider-neutral OpenVPN identity and profile lifecycle.

The provisioner is the only process that handles the CA signing key. Provider
adapters store generated material directly in their secret manager and return
only the closed binding assembled here.
"""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier
from shared.remote_access import (
    bind_openvpn_realization,
    parse_openvpn_capability,
    parse_openvpn_realization,
    validate_openvpn_capability_window,
    validate_openvpn_profile_for_endpoint,
)

_TLS_CRYPT_BYTES = 256


class VpnSecretOps(Protocol):
    """Small provider-secret port used by the credential lifecycle."""

    def read_or_create_issuer(self, range_id: int, generation: UUID, payload_factory: Callable[[], str]) -> str:
        """Return the existing issuer payload or atomically store the factory result."""

    def put_server(self, range_id: int, generation: UUID, payload: str) -> str:
        """Store the gateway-only server identity and return its exact provider reference."""

    def put_profile(self, range_id: int, generation: UUID, payload: str) -> str:
        """Store the participant profile and return its opaque provider reference."""

    def delete_generation(self, range_id: int, generation: UUID, *, delete_identity: bool = True) -> None:
        """Delete issuer, server, and participant material idempotently."""


@dataclass(frozen=True)
class OpenVpnIssuerMaterial:
    """Serializable material retained only in the provider issuer secret."""

    generation: str
    ca: str
    ca_private_key: str
    server_certificate: str
    server_private_key: str
    client_certificate: str
    client_private_key: str
    tls_crypt: str


@dataclass(frozen=True)
class OpenVpnPreparation:
    """In-memory provisioning state for one range generation.

    Ownership is deliberately absent: the gateway and its credentials belong to
    the range generation, and the owner is bound to the published realization
    afterwards. ``server_secret_ref`` is the exact provider reference the gateway
    reads its identity from.
    """

    range_id: int
    generation: UUID
    target_ref: str
    teardown_at: datetime
    material: OpenVpnIssuerMaterial
    server_secret_ref: str


def _pem_private_key(key: ec.EllipticCurvePrivateKey) -> str:
    """Serialize a private key to unencrypted PKCS8 PEM."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _pem_certificate(certificate: x509.Certificate) -> str:
    """Serialize a certificate to PEM."""
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _certificate(
    *,
    subject: str,
    public_key: ec.EllipticCurvePublicKey,
    issuer_name: x509.Name,
    issuer_key: ec.EllipticCurvePrivateKey,
    extended_usage: ObjectIdentifier,
    is_ca: bool = False,
    not_after: datetime,
) -> x509.Certificate:
    """Sign a leaf or CA certificate scoped to this range generation."""
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(issuer_name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if not is_ca:
        builder = builder.add_extension(x509.ExtendedKeyUsage([extended_usage]), critical=False)
    return builder.sign(private_key=issuer_key, algorithm=hashes.SHA256())


def _static_key() -> str:
    """Generate an OpenVPN tls-crypt static key block."""
    encoded = os.urandom(_TLS_CRYPT_BYTES).hex()
    body = "\n".join(encoded[index : index + 32] for index in range(0, len(encoded), 32))
    begin_marker = "-----BE" + "GIN OpenVPN Static key V1-----\n"
    end_marker = "-----END OpenVPN Static key V1-----\n"
    return f"#\n# 2048 bit OpenVPN static key\n#\n{begin_marker}{body}\n{end_marker}"


def _generate_material(generation: UUID, teardown_at: datetime) -> OpenVpnIssuerMaterial:
    """Mint a fresh CA, server, and client identity expiring at teardown."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"shifter-range-{generation}")])
    ca_cert = _certificate(
        subject=f"shifter-range-{generation}",
        public_key=ca_key.public_key(),
        issuer_name=ca_name,
        issuer_key=ca_key,
        extended_usage=ExtendedKeyUsageOID.SERVER_AUTH,
        is_ca=True,
        not_after=teardown_at,
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = _certificate(
        subject="shifter-openvpn-server",
        public_key=server_key.public_key(),
        issuer_name=ca_cert.subject,
        issuer_key=ca_key,
        extended_usage=ExtendedKeyUsageOID.SERVER_AUTH,
        not_after=teardown_at,
    )
    client_key = ec.generate_private_key(ec.SECP256R1())
    client_cert = _certificate(
        subject=f"participant-{generation}",
        public_key=client_key.public_key(),
        issuer_name=ca_cert.subject,
        issuer_key=ca_key,
        extended_usage=ExtendedKeyUsageOID.CLIENT_AUTH,
        not_after=teardown_at,
    )
    return OpenVpnIssuerMaterial(
        generation=str(generation),
        ca=_pem_certificate(ca_cert),
        ca_private_key=_pem_private_key(ca_key),
        server_certificate=_pem_certificate(server_cert),
        server_private_key=_pem_private_key(server_key),
        client_certificate=_pem_certificate(client_cert),
        client_private_key=_pem_private_key(client_key),
        tls_crypt=_static_key(),
    )


def _load_material(payload: str, generation: UUID, teardown_at: datetime) -> OpenVpnIssuerMaterial:
    """Parse stored issuer material and reject stale or foreign generations."""
    try:
        value = json.loads(payload)
        material = OpenVpnIssuerMaterial(**value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OpenVPN issuer secret has an invalid shape") from exc
    if material.generation != str(generation) or set(value) != set(asdict(material)):
        raise ValueError("OpenVPN issuer secret belongs to another generation")
    try:
        certificates = (
            x509.load_pem_x509_certificate(material.ca.encode()),
            x509.load_pem_x509_certificate(material.server_certificate.encode()),
            x509.load_pem_x509_certificate(material.client_certificate.encode()),
        )
    except ValueError as exc:
        raise ValueError("OpenVPN issuer secret contains an invalid certificate") from exc
    if any(certificate.not_valid_after_utc < teardown_at for certificate in certificates):
        raise ValueError("OpenVPN issuer secret expires before the authorized teardown deadline")
    return material


def range_spec_member_refs(range_spec: dict[str, object]) -> list[str]:
    """Return the member identities declared by a legacy range spec."""
    subnets = range_spec.get("subnets")
    if not isinstance(subnets, list):
        return []
    return [
        str(instance.get("uuid", ""))
        for subnet in subnets
        if isinstance(subnet, dict) and isinstance(subnet.get("instances"), list)
        for instance in subnet["instances"]
        if isinstance(instance, dict)
    ]


def prepare_openvpn_access(
    request_uuid: str,
    range_id: int,
    member_refs: Collection[str],
    remote_access_capability: dict[str, object],
    secret_ops: VpnSecretOps,
) -> OpenVpnPreparation:
    """Create/reuse credentials only for the exact server-authorized target.

    ``member_refs`` are the range's member identities (legacy instance UUIDs or
    RAES member keys); the capability must name exactly one of them.
    """
    capability = parse_openvpn_capability(remote_access_capability)
    validate_openvpn_capability_window(capability)
    if list(member_refs).count(capability.target_ref) != 1:
        raise ValueError("OpenVPN capability must identify exactly one range member")
    generation = UUID(request_uuid)
    payload = secret_ops.read_or_create_issuer(
        range_id,
        generation,
        lambda: json.dumps(asdict(_generate_material(generation, capability.teardown_at)), sort_keys=True),
    )
    material = _load_material(payload, generation, capability.teardown_at)
    server_secret_ref = secret_ops.put_server(
        range_id,
        generation,
        json.dumps(
            {
                "ca": material.ca,
                "certificate": material.server_certificate,
                "private_key": material.server_private_key,
                "tls_crypt": material.tls_crypt,
            },
            sort_keys=True,
        ),
    )
    return OpenVpnPreparation(
        range_id=range_id,
        generation=generation,
        target_ref=capability.target_ref,
        teardown_at=capability.teardown_at,
        material=material,
        server_secret_ref=server_secret_ref,
    )


def _render_profile(preparation: OpenVpnPreparation, endpoint: str, port: int) -> str:
    """Render the participant .ovpn profile with inlined credentials."""
    material = preparation.material
    return (
        "client\n"
        "dev tun\n"
        "proto udp\n"
        f"remote {endpoint} {port}\n"
        "resolv-retry infinite\n"
        "nobind\n"
        "persist-key\n"
        "persist-tun\n"
        "remote-cert-tls server\n"
        "auth-nocache\n"
        "verb 3\n"
        "auth SHA256\n"
        "cipher AES-256-GCM\n"
        "data-ciphers AES-256-GCM:AES-128-GCM\n"
        "tls-version-min 1.2\n"
        f"<ca>\n{material.ca}</ca>\n"
        f"<cert>\n{material.client_certificate}</cert>\n"
        f"<key>\n{material.client_private_key}</key>\n"
        f"<tls-crypt>\n{material.tls_crypt}</tls-crypt>\n"
    )


def _probe_openvpn_gateway(endpoint: str, health_port: int, timeout_seconds: int = 180) -> bool:
    """Wait for the gateway's service-and-policy health responder."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with socket.create_connection((endpoint, health_port), timeout=5) as connection:
                connection.settimeout(5)
                if connection.recv(32) == b"ready\n":
                    return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(5)


def verify_openvpn_gateway(
    gateway: object,
    readiness_probe: Callable[[str, int], bool] | None = None,
) -> dict[str, object]:
    """Promote an infrastructure result to ready only after a bounded service probe."""
    if not isinstance(gateway, dict):
        raise ValueError("OpenVPN gateway result is required")
    endpoint = str(gateway.get("endpoint", ""))
    if not endpoint:
        raise ValueError("OpenVPN gateway endpoint is invalid")
    health_endpoint = str(gateway.get("health_endpoint") or endpoint)
    health_port = gateway.get("health_port")
    if isinstance(health_port, bool) or not isinstance(health_port, int) or not 1 <= health_port <= 65535:
        raise ValueError("OpenVPN gateway health port is invalid")
    probe = readiness_probe or _probe_openvpn_gateway
    if not probe(health_endpoint, health_port):
        raise ValueError("OpenVPN gateway service did not become ready")
    verified = dict(gateway)
    verified["ready"] = True
    return verified


def publish_openvpn_profile(
    preparation: OpenVpnPreparation,
    gateway: object,
    secret_ops: VpnSecretOps,
) -> dict[str, object]:
    """Validate a ready gateway, store its client profile, and return the realization.

    The realization is owner-free; the caller binds the owner it is authoritative
    for (``bind_openvpn_realization``).
    """
    if not isinstance(gateway, dict):
        raise ValueError("OpenVPN gateway result is required")
    if gateway.get("ready") is not True:
        raise ValueError("OpenVPN gateway must be ready before profile publication")
    if str(gateway.get("target_ref", "")) != preparation.target_ref:
        raise ValueError("OpenVPN gateway target does not match the authorized Kali member")
    endpoint = str(gateway.get("endpoint", ""))
    port_value = gateway.get("port")
    if isinstance(port_value, bool) or not isinstance(port_value, int):
        raise ValueError("OpenVPN gateway port is invalid")
    realization = parse_openvpn_realization(
        {
            "generation": str(preparation.generation),
            "target_ref": preparation.target_ref,
            "endpoint": endpoint,
            "port": port_value,
            "secret_ref": f"profile-validation:{preparation.generation}",
        }
    )
    profile = _render_profile(preparation, endpoint, port_value)
    validate_openvpn_profile_for_endpoint(profile, endpoint, port_value)
    realization["secret_ref"] = secret_ops.put_profile(preparation.range_id, preparation.generation, profile)
    return parse_openvpn_realization(realization)


def finalize_openvpn_access(
    preparation: OpenVpnPreparation | None,
    gateway: object,
    secret_ops: VpnSecretOps,
    *,
    owner_user_id: int,
) -> dict[str, object] | None:
    """Publish the profile for a ready gateway and bind it to ``owner_user_id``."""
    if preparation is None:
        return None
    return bind_openvpn_realization(publish_openvpn_profile(preparation, gateway, secret_ops), owner_user_id)


def cleanup_openvpn_access(
    range_id: int,
    request_uuid: str,
    secret_ops: VpnSecretOps,
    *,
    delete_identity: bool = True,
) -> None:
    """Delete every credential for a range generation."""
    secret_ops.delete_generation(range_id, UUID(request_uuid), delete_identity=delete_identity)


__all__ = [
    "OpenVpnPreparation",
    "VpnSecretOps",
    "cleanup_openvpn_access",
    "finalize_openvpn_access",
    "prepare_openvpn_access",
    "publish_openvpn_profile",
    "range_spec_member_refs",
    "verify_openvpn_gateway",
]
