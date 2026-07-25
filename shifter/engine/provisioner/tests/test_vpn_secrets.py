"""Provider secret-store adapter tests for OpenVPN generations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from gcp_vpn_identity import gcp_vpn_gateway_pool_service_account_email
from vpn_secrets import AWSVpnSecretOps, GCPVpnSecretOps, openvpn_access_enabled


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "test")


def test_aws_adapter_creates_and_reuses_generation_material(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    generation = uuid4()
    client = MagicMock()
    client.get_secret_value.side_effect = [
        _client_error("ResourceNotFoundException"),
        {"SecretString": "issuer-material"},
        {"SecretString": "issuer-material"},
    ]
    client.create_secret.return_value = {"ARN": "arn:issuer"}
    adapter = AWSVpnSecretOps(client)
    factory = MagicMock(return_value="issuer-material")

    assert adapter.read_or_create_issuer(42, generation, factory) == "issuer-material"
    assert adapter.read_or_create_issuer(42, generation, factory) == "issuer-material"

    factory.assert_called_once_with()
    client.create_secret.assert_called_once()
    create_kwargs = client.create_secret.call_args.kwargs
    assert create_kwargs["Name"].endswith(f"vpn-issuer/range-42/{generation}")
    assert create_kwargs["SecretString"] == "issuer-material"


def test_aws_adapter_deletes_all_generation_secrets_idempotently(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    generation = uuid4()
    client = MagicMock()
    client.delete_secret.side_effect = [None, _client_error("ResourceNotFoundException"), None]

    AWSVpnSecretOps(client).delete_generation(42, generation)

    assert client.delete_secret.call_count == 3
    assert all(call.kwargs["ForceDeleteWithoutRecovery"] is True for call in client.delete_secret.call_args_list)


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


class _InvalidArgument(Exception):
    pass


def _mock_slot_read(monkeypatch, slot: int | None) -> None:
    """Stub the reserved gateway pool-slot DB read used by GCPVpnSecretOps."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None if slot is None else (slot,)
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("vpn_secrets.get_db_connection", MagicMock(return_value=conn))


def _gcp_adapter(client, *, project_id: str = "range-project") -> GCPVpnSecretOps:
    return GCPVpnSecretOps(
        client=client,
        exceptions=SimpleNamespace(NotFound=_NotFound, AlreadyExists=_AlreadyExists, InvalidArgument=_InvalidArgument),
        project_id=project_id,
    )


def test_gcp_server_secret_grants_the_reserved_pool_identity(monkeypatch):
    client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    _mock_slot_read(monkeypatch, 7)
    adapter = _gcp_adapter(client)

    adapter.put_server(42, uuid4(), "server-material")

    client.create_secret.assert_called_once()
    client.add_secret_version.assert_called_once()
    gateway_email = gcp_vpn_gateway_pool_service_account_email("range-project", 7)
    policy = client.set_iam_policy.call_args.kwargs["request"]["policy"]
    assert policy == {
        "bindings": [
            {
                "role": "roles/secretmanager.secretAccessor",
                "members": [f"serviceAccount:{gateway_email}"],
            }
        ]
    }


def test_gcp_adapter_never_creates_or_binds_service_accounts(monkeypatch):
    # ADR-008-R7: the pool model removes runtime SA administration entirely. The
    # adapter holds no IAM-admin client; the only set_iam_policy call is the
    # Secret Manager grant on the server secret (not a service-account resource).
    client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    _mock_slot_read(monkeypatch, 3)
    adapter = _gcp_adapter(client)

    adapter.put_server(42, uuid4(), "server-material")

    assert not hasattr(adapter, "_iam_client")
    assert client.set_iam_policy.call_count == 1
    assert "/secrets/" in client.set_iam_policy.call_args.kwargs["request"]["resource"]


def test_gcp_put_server_raises_when_no_pool_slot_reserved(monkeypatch):
    client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    _mock_slot_read(monkeypatch, None)
    adapter = _gcp_adapter(client)
    generation = uuid4()

    with pytest.raises(RuntimeError, match="gateway pool slot"):
        adapter.put_server(42, generation, "server-material")


def test_gcp_delete_generation_removes_secrets_without_touching_identities():
    client = MagicMock()
    adapter = _gcp_adapter(client)

    adapter.delete_generation(42, uuid4())

    # All three per-generation secrets are deleted; the pooled identity is
    # permanent, so there is no service-account lifecycle to invoke.
    assert client.delete_secret.call_count == 3
    assert not hasattr(adapter, "_iam_client")


def test_capability_gate_requires_selected_provider_prerequisites(monkeypatch):
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.delenv("RANGE_VPN_EDGE_SUBNET_ID", raising=False)
    monkeypatch.delenv("RANGE_VPN_GATEWAY_PERMISSIONS_BOUNDARY_ARN", raising=False)
    monkeypatch.delenv("RANGE_VPN_PROVIDER_ENDPOINT_SECURITY_GROUP_ID", raising=False)
    assert openvpn_access_enabled() is False

    monkeypatch.setenv("RANGE_VPN_EDGE_SUBNET_ID", "subnet-edge")
    monkeypatch.setenv("RANGE_VPN_GATEWAY_PERMISSIONS_BOUNDARY_ARN", "arn:boundary")
    monkeypatch.setenv("RANGE_VPN_PROVIDER_ENDPOINT_SECURITY_GROUP_ID", "sg-endpoints")
    assert openvpn_access_enabled() is False

    monkeypatch.setenv("PORTAL_VPC_CIDR", "10.40.0.0/20")
    assert openvpn_access_enabled() is True

    monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
    monkeypatch.setenv("GCP_RANGE_BACKEND", "gce")
    monkeypatch.setenv("GCP_RANGE_CELL_NETWORK_MODE", "shared-vpc")
    monkeypatch.setenv("GCP_RANGE_PRIVATE_GOOGLE_ACCESS", "true")
    monkeypatch.setenv("GCP_RANGE_HOST_SERVICE_ACCOUNT_EMAIL", "range-host@example.test")
    monkeypatch.setenv("GCP_PROVISIONER_SERVICE_ACCOUNT_EMAIL", "provisioner@example.test")
    monkeypatch.setenv("GCP_RANGE_LINUX_IMAGE", "projects/test/global/images/ubuntu")
    assert openvpn_access_enabled() is True

    monkeypatch.setenv("GCP_RANGE_HOST_SERVICE_ACCOUNT_SCOPES", "https://www.googleapis.com/auth/logging.write")
    assert openvpn_access_enabled() is False
    monkeypatch.setenv("GCP_RANGE_HOST_SERVICE_ACCOUNT_SCOPES", "https://www.googleapis.com/auth/cloud-platform")

    monkeypatch.setenv("GCP_RANGE_CELL_NETWORK_MODE", "vpc-per-range")
    assert openvpn_access_enabled() is False

    monkeypatch.setenv("GCP_RANGE_BACKEND", "gdc")
    assert openvpn_access_enabled() is False
