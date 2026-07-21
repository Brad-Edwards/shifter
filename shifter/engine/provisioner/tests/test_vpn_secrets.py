"""Provider secret-store adapter tests for OpenVPN generations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from botocore.exceptions import ClientError

import vpn_secrets
from gcp_vpn_identity import gcp_vpn_gateway_service_account_email
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


class _FakeBindings(list):
    def add(self):
        binding = SimpleNamespace(role="", members=[])
        self.append(binding)
        return binding


def _gcp_adapter(client, iam_client, *, project_id: str = "range-project") -> GCPVpnSecretOps:
    iam_client.get_iam_policy.return_value = SimpleNamespace(bindings=_FakeBindings())
    return GCPVpnSecretOps(
        client=client,
        iam_client=iam_client,
        exceptions=SimpleNamespace(NotFound=_NotFound, AlreadyExists=_AlreadyExists, InvalidArgument=_InvalidArgument),
        project_id=project_id,
        provisioner_service_account_email=f"provisioner@{project_id}.iam.gserviceaccount.com",
    )


def test_gcp_server_secret_grants_only_the_gateway_service_account():
    client = MagicMock()
    iam_client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    generation = uuid4()
    adapter = _gcp_adapter(client, iam_client)

    adapter.put_server(42, generation, "server-material")

    client.create_secret.assert_called_once()
    client.add_secret_version.assert_called_once()
    iam_client.create_service_account.assert_called_once()
    gateway_email = gcp_vpn_gateway_service_account_email("range-project", 42, generation)
    iam_client.get_iam_policy.assert_called_once_with(
        request={"resource": f"projects/range-project/serviceAccounts/{gateway_email}"}
    )
    act_as_policy = iam_client.set_iam_policy.call_args.kwargs["request"]["policy"]
    assert [(binding.role, list(binding.members)) for binding in act_as_policy.bindings] == [
        (
            "roles/iam.serviceAccountUser",
            ["serviceAccount:provisioner@range-project.iam.gserviceaccount.com"],
        )
    ]
    policy = client.set_iam_policy.call_args.kwargs["request"]["policy"]
    assert policy == {
        "bindings": [
            {
                "role": "roles/secretmanager.secretAccessor",
                "members": [f"serviceAccount:{gateway_email}"],
            }
        ]
    }


def test_gcp_server_secret_retries_gateway_identity_propagation(monkeypatch):
    client = MagicMock()
    iam_client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    generation = uuid4()
    adapter = _gcp_adapter(client, iam_client)
    gateway_email = gcp_vpn_gateway_service_account_email("range-project", 42, generation)
    client.set_iam_policy.side_effect = [
        _InvalidArgument(f"400 Service account {gateway_email} does not exist."),
        None,
    ]
    sleep = MagicMock()
    monkeypatch.setattr(vpn_secrets.time, "sleep", sleep)

    adapter.put_server(42, generation, "server-material")

    assert client.set_iam_policy.call_count == 2
    sleep.assert_called_once_with(adapter._SECRET_IAM_PROPAGATION_RETRY_SECONDS)


def test_gcp_generations_get_distinct_gateway_identities_and_cleanup():
    client = MagicMock()
    iam_client = MagicMock()
    client.access_secret_version.side_effect = _NotFound()
    adapter = _gcp_adapter(client, iam_client)
    first = uuid4()
    second = uuid4()

    adapter.put_server(42, first, "first")
    adapter.put_server(42, second, "second")
    members = [
        call.kwargs["request"]["policy"]["bindings"][0]["members"][0] for call in client.set_iam_policy.call_args_list
    ]
    assert members[0] != members[1]

    adapter.delete_generation(42, first)
    iam_client.delete_service_account.assert_called_once_with(
        request={
            "name": (
                "projects/range-project/serviceAccounts/"
                f"{gcp_vpn_gateway_service_account_email('range-project', 42, first)}"
            )
        }
    )


def test_gcp_compensation_preserves_identity_for_same_generation_retry():
    client = MagicMock()
    iam_client = MagicMock()
    adapter = _gcp_adapter(client, iam_client)

    adapter.delete_generation(42, uuid4(), delete_identity=False)

    assert client.delete_secret.call_count == 3
    iam_client.delete_service_account.assert_not_called()


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
