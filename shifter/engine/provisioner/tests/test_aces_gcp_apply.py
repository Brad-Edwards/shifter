"""Tests for ACES-native GCE range-cell provisioning orchestration (ADR-031/032).

Exercises the full apply/destroy path against a fake Compute API + injected SSH
secret ops (no real GCP): provision creates network/subnets/firewalls/addresses/
instances; the provisioner-managed SSH key is minted per instance (never a
scenario secret); reconcile skips re-insert; apply cleans up on failure; and
destroy deletes every owned resource + secret.
"""

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aces_account_credentials import AcesAccountCredentialOps, install_instance_account_credentials
from aces_gcp_apply import AcesGceSecretOps, apply_aces_range_cell, destroy_aces_range_cell
from aces_gcp_plan import AcesGcePlanError
from aces_plan import AcesPlan, AcesPlanAccount, AcesPlanContent, AcesPlanImage, AcesPlanNetwork, AcesPlanNode
from config import GCERangeCellConfig, GCERangeImageProfile
from executors.base import CommandResult
from executors.factory import GuestExecutionContext


class _NotFound(Exception):
    """Fake Google NotFound exception."""


def _config(network_mode: str = "vpc-per-range") -> GCERangeCellConfig:
    return GCERangeCellConfig(
        project_id="proj-1",
        region="us-east1",
        zone="us-east1-b",
        network_mode=network_mode,
        network_id="projects/proj-1/global/networks/shared" if network_mode == "shared-vpc" else "",
        service_account_email="host@proj-1.iam.gserviceaccount.com",
        portal_network_cidrs=("203.0.113.0/24",),
    )


def _plan() -> AcesPlan:
    node = AcesPlanNode(
        address="node.web",
        name="web",
        os_family="linux",
        count=2,
        network_addresses=("net.lan",),
        image=AcesPlanImage(name="ubuntu"),
    )
    network = AcesPlanNetwork(address="net.lan", name="lan", cidr="10.9.0.0/24")
    return AcesPlan(aces_sdl_version="0.19.1", nodes=(node,), networks=(network,))


def _resolver(node):
    return GCERangeImageProfile(source_image="projects/x/global/images/ubuntu-1")


def _clients(*, exists: bool = False, instance_insert_error: Exception | None = None) -> SimpleNamespace:
    def get_side_effect(**_kwargs):
        if exists:
            return SimpleNamespace(metadata=SimpleNamespace(items=[]))
        raise _NotFound()

    def service(insert_error: Exception | None = None):
        svc = MagicMock()
        svc.get.side_effect = get_side_effect
        if insert_error is not None:
            svc.insert.side_effect = insert_error
        else:
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
        instances=service(instance_insert_error),
        global_operations=op_service,
        region_operations=op_service,
        zone_operations=op_service,
        google_exceptions=SimpleNamespace(NotFound=_NotFound),
    )


def _secret_ops() -> tuple[AcesGceSecretOps, SimpleNamespace]:
    mocks = SimpleNamespace(
        ensure_ssh=MagicMock(return_value=("projects/proj-1/secrets/ssh", "ssh-ed25519 AAAAKEY")),
        delete_ssh=MagicMock(),
    )
    return AcesGceSecretOps(ensure_ssh=mocks.ensure_ssh, delete_ssh=mocks.delete_ssh), mocks


def _account_secret_ops() -> tuple[AcesAccountCredentialOps, SimpleNamespace]:
    mocks = SimpleNamespace(
        ensure_password=MagicMock(return_value=("projects/proj-1/secrets/password", "PASSWORD")),
        ensure_public_key=MagicMock(return_value=("projects/proj-1/secrets/key", "ssh-rsa PUBLIC")),
        delete=MagicMock(),
    )
    return (
        AcesAccountCredentialOps(
            ensure_password=mocks.ensure_password,
            ensure_public_key=mocks.ensure_public_key,
            delete=mocks.delete,
        ),
        mocks,
    )


class TestApply:
    def test_provisions_network_subnet_firewall_and_instances(self):
        clients = _clients()
        secret_ops, secret_mocks = _secret_ops()
        output = apply_aces_range_cell("req-1", 7, _plan(), _resolver, _config(), clients, secret_ops)

        assert clients.networks.insert.called  # vpc-per-range manages its own VPC
        assert clients.subnetworks.insert.call_count == 1
        assert clients.firewalls.insert.called
        # count=2 -> two instances, two reserved addresses, two SSH secrets.
        assert clients.addresses.insert.call_count == 2
        assert clients.instances.insert.call_count == 2
        assert secret_mocks.ensure_ssh.call_count == 2
        assert len(output["instances"]) == 2
        assert set(output["subnets"]) == {"lan"}

    def test_ssh_secret_keyed_on_aces_instance_not_scenario(self):
        clients = _clients()
        secret_ops, secret_mocks = _secret_ops()
        apply_aces_range_cell("req-1", 7, _plan(), _resolver, _config(), clients, secret_ops)
        keys = sorted(call.args[1] for call in secret_mocks.ensure_ssh.call_args_list)
        assert keys == ["node.web#0", "node.web#1"]

    def test_shared_vpc_does_not_create_network(self):
        clients = _clients()
        secret_ops, _ = _secret_ops()
        apply_aces_range_cell("req-1", 7, _plan(), _resolver, _config("shared-vpc"), clients, secret_ops)
        assert not clients.networks.insert.called

    def test_reconcile_existing_instance_skips_insert(self):
        clients = _clients(exists=True)
        secret_ops, _ = _secret_ops()
        apply_aces_range_cell("req-1", 7, _plan(), _resolver, _config(), clients, secret_ops)
        assert not clients.instances.insert.called

    def test_apply_failure_triggers_cleanup_and_reraises(self):
        clients = _clients(instance_insert_error=RuntimeError("boom"))
        secret_ops, secret_mocks = _secret_ops()
        with pytest.raises(RuntimeError, match="boom"):
            apply_aces_range_cell("req-1", 7, _plan(), _resolver, _config(), clients, secret_ops)
        # Cleanup ran: the reconstructive destroy sweeps every instance's SSH
        # secret unconditionally (nothing exists yet to GCE-delete since the first
        # insert failed, so instances.delete is legitimately not reached).
        assert secret_mocks.delete_ssh.called
        assert clients.instances.get.called


def _plan_with_content(*content: AcesPlanContent) -> AcesPlan:
    node = AcesPlanNode(
        address="node.web",
        name="web",
        os_family="linux",
        count=1,
        network_addresses=("net.lan",),
        image=AcesPlanImage(name="ubuntu"),
    )
    network = AcesPlanNetwork(address="net.lan", name="lan", cidr="10.9.0.0/24")
    return AcesPlan(aces_sdl_version="0.19.1", nodes=(node,), networks=(network,), content=content)


class TestCompositionIntegration:
    def _startup_script(self, clients) -> str:
        body = clients.instances.insert.call_args.kwargs["instance_resource"]
        items = body["metadata"]["items"]
        return next(item["value"] for item in items if item["key"] == "startup-script")

    def test_composition_reaches_instance_startup_script(self):
        content = AcesPlanContent(
            name="doc", content_type="file", target_address="node.web", path="/srv/x.txt", text="hello"
        )
        clients = _clients()
        secret_ops, _ = _secret_ops()
        apply_aces_range_cell("req-1", 7, _plan_with_content(content), _resolver, _config(), clients, secret_ops)
        startup = self._startup_script(clients)
        import base64

        assert base64.b64encode(b"hello").decode() in startup
        assert "chmod 644 /srv/x.txt" in startup

    def test_orphan_composition_target_fails_closed(self):
        content = AcesPlanContent(name="doc", content_type="file", target_address="node.ghost", path="/srv/x", text="h")
        clients = _clients()
        secret_ops, _ = _secret_ops()
        with pytest.raises(AcesGcePlanError, match="not present in this plan"):
            apply_aces_range_cell("req-1", 7, _plan_with_content(content), _resolver, _config(), clients, secret_ops)


def _plan_with_accounts(*accounts: AcesPlanAccount, os_family: str = "linux", count: int = 2) -> AcesPlan:
    plan = _plan()
    return AcesPlan(
        aces_sdl_version=plan.aces_sdl_version,
        nodes=(replace(plan.nodes[0], os_family=os_family, count=count),),
        networks=plan.networks,
        accounts=accounts,
    )


class _RecordingCredentialExecutor:
    def __init__(self):
        self.scripts: list[str] = []
        self.closed = False

    def wait_for_ready(self, target, timeout_seconds, document_name):
        return True

    def run_command(self, instance_id, script, timeout_seconds, document_name, stdin_input=None):
        self.scripts.append(script)
        return CommandResult(success=True, exit_code=0, stdout="", stderr="")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("os_family", ["linux", "windows"])
def test_normal_apply_path_realizes_both_account_auth_methods_without_output_exposure(os_family: str):
    accounts = (
        AcesPlanAccount(
            username="alice",
            target_address="node.web",
            auth_method="password",
            password_strength="strong",
        ),
        AcesPlanAccount(username="bob", target_address="node.web", auth_method="publickey"),
    )
    clients = _clients()
    ssh_ops, _ = _secret_ops()
    account_ops, _ = _account_secret_ops()
    executors: list[_RecordingCredentialExecutor] = []

    def execution_builder(instance_output, **_kwargs):
        executor = _RecordingCredentialExecutor()
        executors.append(executor)
        return GuestExecutionContext(
            executor=executor,
            target=instance_output["private_ip"],
            document_name="AWS-RunPowerShellScript" if os_family == "windows" else "AWS-RunShellScript",
            transport_name="ssh",
        )

    def credential_installer(**kwargs):
        install_instance_account_credentials(**kwargs, execution_builder=execution_builder)

    output = apply_aces_range_cell(
        "req-1",
        7,
        _plan_with_accounts(*accounts, os_family=os_family, count=1),
        _resolver,
        _config(),
        clients,
        ssh_ops,
        account_secret_ops=account_ops,
        credential_installer=credential_installer,
    )

    rendered_scripts = "\n".join(executors[0].scripts)
    assert "PASSWORD" in rendered_scripts
    assert "ssh-rsa PUBLIC" in rendered_scripts
    if os_family == "windows":
        assert "Set-LocalUser" in rendered_scripts
        assert "$Username = 'bob'" in rendered_scripts
        assert "Match User $Username" in rendered_scripts
        assert "authorizedkeysfile" in rendered_scripts.lower()
    else:
        assert "chpasswd" in rendered_scripts
        assert "chmod 600" in rendered_scripts
        assert "grep -Fqx" in rendered_scripts
    assert executors[0].closed is True
    assert "PASSWORD" not in repr(output)
    assert "ssh-rsa PUBLIC" not in repr(output)
    assert "projects/proj-1/secrets/password" not in repr(output)


class TestAccountCredentialIntegration:
    def test_installs_each_target_account_on_every_concrete_node_instance(self):
        account = AcesPlanAccount(
            username="alice",
            target_address="node.web",
            auth_method="password",
            password_strength="strong",
        )
        clients = _clients()
        ssh_ops, _ = _secret_ops()
        account_ops, _ = _account_secret_ops()
        installer = MagicMock()

        output = apply_aces_range_cell(
            "req-1",
            7,
            _plan_with_accounts(account),
            _resolver,
            _config(),
            clients,
            ssh_ops,
            account_secret_ops=account_ops,
            credential_installer=installer,
        )

        assert installer.call_count == 2
        assert [call.kwargs["instance_key"] for call in installer.call_args_list] == ["node.web#0", "node.web#1"]
        assert all(call.kwargs["accounts"] == (account,) for call in installer.call_args_list)
        serialized_output = repr(output)
        assert "PASSWORD" not in serialized_output
        assert "projects/proj-1/secrets/password" not in serialized_output

    def test_accounts_for_other_nodes_are_not_installed(self):
        account = AcesPlanAccount(username="alice", target_address="node.other")
        clients = _clients()
        ssh_ops, _ = _secret_ops()
        account_ops, _ = _account_secret_ops()
        installer = MagicMock()

        with pytest.raises(AcesGcePlanError, match="not present in this plan"):
            apply_aces_range_cell(
                "req-1",
                7,
                _plan_with_accounts(account),
                _resolver,
                _config(),
                clients,
                ssh_ops,
                account_secret_ops=account_ops,
                credential_installer=installer,
            )

        installer.assert_not_called()

    def test_reconcile_existing_instances_reapplies_account_credentials(self):
        account = AcesPlanAccount(username="alice", target_address="node.web", auth_method="password")
        clients = _clients(exists=True)
        ssh_ops, _ = _secret_ops()
        account_ops, _ = _account_secret_ops()
        installer = MagicMock()

        apply_aces_range_cell(
            "req-1",
            7,
            _plan_with_accounts(account),
            _resolver,
            _config(),
            clients,
            ssh_ops,
            account_secret_ops=account_ops,
            credential_installer=installer,
        )

        assert not clients.instances.insert.called
        assert [call.kwargs["instance_key"] for call in installer.call_args_list] == ["node.web#0", "node.web#1"]

    def test_credential_install_failure_cleans_up_every_account_secret(self):
        account = AcesPlanAccount(username="alice", target_address="node.web", auth_method="publickey")
        clients = _clients()
        ssh_ops, ssh_mocks = _secret_ops()
        account_ops, account_mocks = _account_secret_ops()
        installer = MagicMock(side_effect=RuntimeError("credential setup failed"))

        with pytest.raises(RuntimeError, match="credential setup failed"):
            apply_aces_range_cell(
                "req-1",
                7,
                _plan_with_accounts(account),
                _resolver,
                _config(),
                clients,
                ssh_ops,
                account_secret_ops=account_ops,
                credential_installer=installer,
            )

        assert ssh_mocks.delete_ssh.call_count == 2
        assert account_mocks.delete.call_count == 2
        assert [call.args[1] for call in account_mocks.delete.call_args_list] == ["node.web#1", "node.web#0"]


class TestDestroy:
    def test_deletes_instances_addresses_firewalls_subnets_network_and_secrets(self):
        clients = _clients(exists=True)
        secret_ops, secret_mocks = _secret_ops()
        destroy_aces_range_cell("req-1", 7, _plan(), _config(), clients, secret_ops)

        assert clients.instances.delete.call_count == 2
        assert clients.addresses.delete.call_count == 2
        assert clients.firewalls.delete.called
        assert clients.subnetworks.delete.call_count == 1
        assert clients.networks.delete.called  # vpc-per-range owns the VPC
        assert secret_mocks.delete_ssh.call_count == 2

    def test_shared_vpc_destroy_keeps_network(self):
        clients = _clients(exists=True)
        secret_ops, _ = _secret_ops()
        destroy_aces_range_cell("req-1", 7, _plan(), _config("shared-vpc"), clients, secret_ops)
        assert not clients.networks.delete.called

    def test_deletes_every_per_instance_authored_account_secret(self):
        account = AcesPlanAccount(username="alice", target_address="node.web", auth_method="publickey")
        clients = _clients(exists=True)
        ssh_ops, _ = _secret_ops()
        account_ops, account_mocks = _account_secret_ops()

        destroy_aces_range_cell(
            "req-1",
            7,
            _plan_with_accounts(account),
            _config(),
            clients,
            ssh_ops,
            account_secret_ops=account_ops,
        )

        assert account_mocks.delete.call_count == 2
        assert [call.args[1] for call in account_mocks.delete.call_args_list] == ["node.web#1", "node.web#0"]
