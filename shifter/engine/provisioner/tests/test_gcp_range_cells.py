"""Tests for the GCE range-cell backend."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from config import GCERangeCellConfig, GCERangeImageProfile
from gcp_range_cells import (
    GCEGuestSecretOps,
    _build_clients,
    apply_range_cell,
    destroy_range_cell,
    render_range_cell_plan,
)
from state_helpers import _build_instance_state


class NotFound(Exception):
    """Fake Google NotFound exception."""


_POLARIS_SUBNETWORK_SELF_LINK = "projects/test-project/regions/us-central1/subnetworks/shifter-r-42-polaris"


def _sample_config() -> GCERangeCellConfig:
    return GCERangeCellConfig(
        project_id="test-project",
        region="us-central1",
        zone="us-central1-b",
        network_mode="vpc-per-range",
        service_account_email="range-host@test-project.iam.gserviceaccount.com",
        linux=GCERangeImageProfile(
            source_image="projects/debian-cloud/global/images/family/debian-12",
            machine_type="e2-standard-2",
            disk_size_gb=50,
        ),
        kali=GCERangeImageProfile(
            source_image="projects/kali/global/images/kali",
            machine_type="e2-standard-4",
            disk_size_gb=80,
        ),
        dc=GCERangeImageProfile(
            source_image="projects/windows-cloud/global/images/family/windows-2022",
            machine_type="e2-standard-4",
            disk_size_gb=100,
        ),
        portal_network_cidrs=("10.40.0.0/20",),
    )


def _variables() -> dict:
    return {
        "range_id": 42,
        "request_uuid": "req-123",
        "subnets": [
            {
                "name": "polaris",
                "uuid": "subnet-uuid",
                "cidr": "10.50.2.0/28",
                "instances": [
                    {
                        "uuid": "linux-uuid",
                        "name": "kali",
                        "role": "attacker",
                        "os_type": "kali",
                    },
                    {
                        "uuid": "dc-uuid",
                        "name": "dc01",
                        "role": "dc",
                        "os_type": "windows",
                    },
                ],
            }
        ],
    }


def _mock_clients(*, exists: bool = False) -> SimpleNamespace:
    def get_side_effect(**_kwargs):
        if exists:
            return object()
        raise NotFound()

    def service():
        svc = MagicMock()
        svc.get.side_effect = get_side_effect
        svc.insert.return_value = SimpleNamespace(name="op")
        svc.delete.return_value = SimpleNamespace(name="op")
        return svc

    op_service = MagicMock()
    op_service.wait.return_value = SimpleNamespace(status="DONE")
    return SimpleNamespace(
        networks=service(),
        subnetworks=service(),
        firewalls=service(),
        addresses=service(),
        instances=service(),
        global_operations=op_service,
        region_operations=op_service,
        zone_operations=op_service,
        google_exceptions=SimpleNamespace(NotFound=NotFound),
    )


def _mock_secret_ops(mocker) -> tuple[GCEGuestSecretOps, SimpleNamespace]:
    mocks = SimpleNamespace(
        ensure_ssh=mocker.Mock(return_value=("projects/test/secrets/ssh", "ssh-ed25519 AAAA")),
        ensure_rdp_password=mocker.Mock(return_value=("projects/test/secrets/rdp", "Password-1")),
        delete_ssh=mocker.Mock(),
        delete_rdp_password=mocker.Mock(),
    )
    return (
        GCEGuestSecretOps(
            ensure_ssh=mocks.ensure_ssh,
            ensure_rdp_password=mocks.ensure_rdp_password,
            delete_ssh=mocks.delete_ssh,
            delete_rdp_password=mocks.delete_rdp_password,
        ),
        mocks,
    )


def test_render_range_cell_plan_uses_vpc_per_range_and_deterministic_ips():
    plan = render_range_cell_plan("req-123", _variables(), _sample_config())

    assert plan["network"]["name"] == "shifter-range-42"
    assert plan["subnets"][0]["resource_name"] == "shifter-r-42-polaris"
    assert plan["subnets"][0]["ip_assignments"] == {
        "linux-uuid": "10.50.2.3",
        "dc-uuid": "10.50.2.4",
    }
    assert [instance["private_ip"] for instance in plan["instances"]] == ["10.50.2.3", "10.50.2.4"]
    assert {firewall["name"] for firewall in plan["firewalls"]} >= {
        "shifter-r-42-polaris-ingress",
        "shifter-r-42-mgmt",
        "shifter-r-42-egress-internal",
        "shifter-r-42-egress-deny",
    }


def test_apply_range_cell_is_idempotent_when_resources_exist(mocker):
    clients = _mock_clients(exists=True)
    secret_ops, _mocks = _mock_secret_ops(mocker)

    output = apply_range_cell(
        "req-123",
        _variables(),
        config=_sample_config(),
        clients=clients,
        secret_ops=secret_ops,
    )

    assert output == {
        "subnets": {
            "polaris": {
                "uuid": "subnet-uuid",
                "subnet_id": "shifter-r-42-polaris",
                "subnet_cidr": "10.50.2.0/28",
                "gcp_network_name": "shifter-range-42",
                "gcp_network_self_link": "projects/test-project/global/networks/shifter-range-42",
                "gcp_subnetwork_name": "shifter-r-42-polaris",
                "gcp_subnetwork_self_link": _POLARIS_SUBNETWORK_SELF_LINK,
                "gcp_region": "us-central1",
                "gcp_gateway_reserved": True,
                "gcp_instance_ip_assignments": {
                    "linux-uuid": "10.50.2.3",
                    "dc-uuid": "10.50.2.4",
                },
            }
        },
        "instances": [
            {
                "uuid": "linux-uuid",
                "name": "kali",
                "asset_type": "gce_vm",
                "role": "attacker",
                "os": "kali",
                "subnet_name": "polaris",
                "instance_id": "shifter-r-42-polaris-kali",
                "private_ip": "10.50.2.3",
                "ssh_key_secret_arn": "projects/test/secrets/ssh",
                "ssh_username": "kali",
                "gcp_project_id": "test-project",
                "gcp_region": "us-central1",
                "gcp_zone": "us-central1-b",
                "gcp_network_name": "shifter-range-42",
                "gcp_network_self_link": "projects/test-project/global/networks/shifter-range-42",
                "gcp_subnetwork_name": "shifter-r-42-polaris",
                "gcp_subnetwork_self_link": _POLARIS_SUBNETWORK_SELF_LINK,
                "gcp_instance_name": "shifter-r-42-polaris-kali",
                "gcp_address_name": "shifter-r-42-polaris-kali-ip",
                "gcp_network_tags": ["shifter-range-42", "shifter-range-42-polaris", "shifter-role-attacker"],
                "gcp_service_account_email": "range-host@test-project.iam.gserviceaccount.com",
                "rdp_password_secret_arn": "projects/test/secrets/rdp",
                "gcp_rdp_password_secret_ref": "projects/test/secrets/rdp",
            },
            {
                "uuid": "dc-uuid",
                "name": "dc01",
                "asset_type": "gce_vm",
                "role": "dc",
                "os": "windows",
                "subnet_name": "polaris",
                "instance_id": "shifter-r-42-polaris-dc01",
                "private_ip": "10.50.2.4",
                "ssh_key_secret_arn": "projects/test/secrets/ssh",
                "ssh_username": "Administrator",
                "gcp_project_id": "test-project",
                "gcp_region": "us-central1",
                "gcp_zone": "us-central1-b",
                "gcp_network_name": "shifter-range-42",
                "gcp_network_self_link": "projects/test-project/global/networks/shifter-range-42",
                "gcp_subnetwork_name": "shifter-r-42-polaris",
                "gcp_subnetwork_self_link": _POLARIS_SUBNETWORK_SELF_LINK,
                "gcp_instance_name": "shifter-r-42-polaris-dc01",
                "gcp_address_name": "shifter-r-42-polaris-dc01-ip",
                "gcp_network_tags": ["shifter-range-42", "shifter-range-42-polaris", "shifter-role-dc"],
                "gcp_service_account_email": "range-host@test-project.iam.gserviceaccount.com",
            },
        ],
    }
    clients.networks.insert.assert_not_called()
    clients.subnetworks.insert.assert_not_called()
    clients.firewalls.insert.assert_not_called()
    clients.addresses.insert.assert_not_called()
    clients.instances.insert.assert_not_called()


def test_apply_range_cell_cleans_up_on_failure(mocker):
    clients = _mock_clients(exists=False)
    clients.instances.insert.side_effect = RuntimeError("insert failed")
    secret_ops, _mocks = _mock_secret_ops(mocker)
    cleanup = mocker.Mock()

    with pytest.raises(RuntimeError, match="insert failed"):
        apply_range_cell(
            "req-123",
            _variables(),
            config=_sample_config(),
            clients=clients,
            secret_ops=secret_ops,
            cleanup_range_cell=cleanup,
        )

    cleanup.assert_called_once_with("req-123", _variables())


def test_build_clients_uses_google_compute_default_classes(mocker, monkeypatch):
    compute_module = ModuleType("google.cloud.compute_v1")
    network = object()
    subnetwork = object()
    firewall = object()
    address = object()
    instance = object()
    global_operations = object()
    region_operations = object()
    zone_operations = object()
    compute_module.NetworksClient = mocker.Mock(return_value=network)
    compute_module.SubnetworksClient = mocker.Mock(return_value=subnetwork)
    compute_module.FirewallsClient = mocker.Mock(return_value=firewall)
    compute_module.AddressesClient = mocker.Mock(return_value=address)
    compute_module.InstancesClient = mocker.Mock(return_value=instance)
    compute_module.GlobalOperationsClient = mocker.Mock(return_value=global_operations)
    compute_module.RegionOperationsClient = mocker.Mock(return_value=region_operations)
    compute_module.ZoneOperationsClient = mocker.Mock(return_value=zone_operations)
    exceptions_module = ModuleType("google.api_core.exceptions")
    exceptions_module.NotFound = NotFound
    monkeypatch.setitem(sys.modules, "google", ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", ModuleType("google.cloud"))
    monkeypatch.setitem(sys.modules, "google.cloud.compute_v1", compute_module)
    monkeypatch.setitem(sys.modules, "google.api_core", ModuleType("google.api_core"))
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions_module)

    clients = _build_clients()

    assert clients.networks is network
    assert clients.subnetworks is subnetwork
    assert clients.firewalls is firewall
    assert clients.addresses is address
    assert clients.instances is instance
    assert clients.global_operations is global_operations
    assert clients.region_operations is region_operations
    assert clients.zone_operations is zone_operations
    assert clients.google_exceptions is exceptions_module


def test_apply_range_cell_raises_on_completed_operation_error(mocker):
    clients = _mock_clients(exists=False)
    clients.global_operations.wait.return_value = {
        "error": {"errors": [{"code": "QUOTA_EXCEEDED", "message": "too many networks"}]}
    }
    secret_ops, _mocks = _mock_secret_ops(mocker)
    cleanup = mocker.Mock()

    with pytest.raises(RuntimeError, match="QUOTA_EXCEEDED: too many networks"):
        apply_range_cell(
            "req-123",
            _variables(),
            config=_sample_config(),
            clients=clients,
            secret_ops=secret_ops,
            cleanup_range_cell=cleanup,
        )

    cleanup.assert_called_once_with("req-123", _variables())


def test_destroy_range_cell_deletes_every_resource(mocker):
    clients = _mock_clients(exists=True)
    secret_ops, mocks = _mock_secret_ops(mocker)
    order = MagicMock()
    order.attach_mock(clients.instances.delete, "delete_instance")
    order.attach_mock(clients.addresses.delete, "delete_address")
    order.attach_mock(clients.firewalls.delete, "delete_firewall")
    order.attach_mock(clients.subnetworks.delete, "delete_subnetwork")
    order.attach_mock(clients.networks.delete, "delete_network")

    destroy_range_cell("req-123", _variables(), config=_sample_config(), clients=clients, secret_ops=secret_ops)

    assert clients.instances.delete.call_count == 2
    assert clients.addresses.delete.call_count == 2
    assert clients.firewalls.delete.call_count == 4
    assert clients.subnetworks.delete.call_count == 1
    clients.networks.delete.assert_called_once()
    assert mocks.delete_ssh.call_count == 2
    assert mocks.delete_rdp_password.call_count == 2
    assert order.mock_calls == [
        call.delete_instance(project="test-project", zone="us-central1-b", instance="shifter-r-42-polaris-dc01"),
        call.delete_address(project="test-project", region="us-central1", address="shifter-r-42-polaris-dc01-ip"),
        call.delete_instance(project="test-project", zone="us-central1-b", instance="shifter-r-42-polaris-kali"),
        call.delete_address(project="test-project", region="us-central1", address="shifter-r-42-polaris-kali-ip"),
        call.delete_firewall(project="test-project", firewall="shifter-r-42-egress-deny"),
        call.delete_firewall(project="test-project", firewall="shifter-r-42-egress-internal"),
        call.delete_firewall(project="test-project", firewall="shifter-r-42-mgmt"),
        call.delete_firewall(project="test-project", firewall="shifter-r-42-polaris-ingress"),
        call.delete_subnetwork(project="test-project", region="us-central1", subnetwork="shifter-r-42-polaris"),
        call.delete_network(project="test-project", network="shifter-range-42"),
    ]


def test_gce_output_preserves_provider_metadata_for_db_state(mocker):
    clients = _mock_clients(exists=True)
    secret_ops, _mocks = _mock_secret_ops(mocker)

    output = apply_range_cell(
        "req-123",
        _variables(),
        config=_sample_config(),
        clients=clients,
        secret_ops=secret_ops,
    )

    state = _build_instance_state(output["instances"][0], provider="gcp")

    assert state["asset_type"] == "gce_vm"
    assert state["provider_metadata"]["gcp"]["instance_name"] == "shifter-r-42-polaris-kali"
    assert state["provider_metadata"]["gcp"]["network_name"] == "shifter-range-42"
    assert state["provider_metadata"]["gcp"]["network_tags"] == [
        "shifter-range-42",
        "shifter-range-42-polaris",
        "shifter-role-attacker",
    ]
