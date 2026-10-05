"""Participant OpenVPN credentials on the RAES GCE path (#2030, ADR-039-R10).

Drives ``raes_openvpn``: credentials are minted against the operation input's
capability and projected gateway slot before any gateway exists, the gateway is
published only after its health probe, and destroy removes every credential of
the generation. The provider secret store is an in-memory double.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.raes.operation_input import RaesRemoteAccess
from shared.remote_access import build_openvpn_capability

import raes_openvpn
from raes_gcp_network_allocation import RaesRealizationError
from raes_gcp_vpn_plan import RaesGceRemoteAccess

_REQUEST_ID = "11111111-2222-3333-4444-555555555555"
_TARGET = "provision.node.kali#0"


class _MemorySecretOps:
    """Secret-store double that records the gateway slot it was bound to."""

    def __init__(self, *, gateway_pool_slot=None) -> None:
        self.gateway_pool_slot = gateway_pool_slot
        self.values: dict[str, str] = {}
        self.deleted: list[tuple[int, UUID]] = []

    def read_or_create_issuer(self, range_id, generation, payload_factory):
        return self.values.setdefault(f"issuer:{range_id}:{generation}", payload_factory())

    def put_server(self, range_id, generation, payload):
        ref = f"projects/secrets-proj/secrets/server-{range_id}-{generation}"
        self.values[ref] = payload
        return ref

    def put_profile(self, range_id, generation, payload):
        ref = f"projects/secrets-proj/secrets/profile-{range_id}-{generation}"
        self.values[ref] = payload
        return ref

    def delete_generation(self, range_id, generation, *, delete_identity=True):
        self.deleted.append((range_id, generation))


@pytest.fixture
def secret_store(monkeypatch):
    stores: list[_MemorySecretOps] = []

    def factory(**kwargs):
        stores.append(_MemorySecretOps(**kwargs))
        return stores[-1]

    monkeypatch.setattr(raes_openvpn, "GCPVpnSecretOps", factory)
    monkeypatch.setattr(raes_openvpn, "openvpn_access_enabled", lambda: True)
    return stores


def _remote(target: str = _TARGET) -> RaesRemoteAccess:
    return RaesRemoteAccess(
        capability=build_openvpn_capability(target, datetime.now(UTC) + timedelta(days=1)),
        gateway_pool_slot=5,
    )


def _plan():
    kali = SimpleNamespace(address="provision.node.kali", count=1)
    victims = SimpleNamespace(address="provision.node.victim", count=2)
    return SimpleNamespace(nodes=(kali, victims))


def _gateway(**overrides):
    return {
        "endpoint": "34.1.2.3",
        "port": 1194,
        "health_endpoint": "10.9.0.200",
        "health_port": 1195,
        "target_ref": _TARGET,
        "ready": False,
        **overrides,
    }


class TestPrepare:
    def test_a_range_without_remote_access_prepares_nothing(self, secret_store):
        assert raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), None) is None
        assert secret_store == []

    def test_an_installation_that_cannot_realize_the_gateway_fails_closed(self, monkeypatch, secret_store):
        monkeypatch.setattr(raes_openvpn, "openvpn_access_enabled", lambda: False)
        with pytest.raises(RaesRealizationError, match="not configured"):
            raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), _remote())
        assert secret_store == []

    def test_credentials_are_bound_to_the_projected_slot_and_exact_server_secret(self, secret_store):
        session = raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), _remote())

        (store,) = secret_store
        assert store.gateway_pool_slot == 5
        planned = session.plan_remote_access()
        assert planned == RaesGceRemoteAccess(_TARGET, 5, f"projects/secrets-proj/secrets/server-7-{_REQUEST_ID}")
        assert planned.names_only() == RaesGceRemoteAccess(_TARGET, 5)

    def test_a_capability_for_a_member_the_plan_does_not_declare_is_rejected(self, secret_store):
        with pytest.raises(ValueError, match="exactly one range member"):
            raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), _remote("provision.node.other#0"))


class TestPublish:
    def test_a_healthy_gateway_publishes_an_owner_free_realization(self, monkeypatch, secret_store):
        probes: list = []
        monkeypatch.setattr(
            "vpn_access._probe_openvpn_gateway", lambda endpoint, port: probes.append((endpoint, port)) or True
        )
        session = raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), _remote())

        realization = session.publish(_gateway())

        assert probes == [("10.9.0.200", 1195)]
        assert realization == {
            "generation": _REQUEST_ID,
            "target_ref": _TARGET,
            "endpoint": "34.1.2.3",
            "port": 1194,
            "secret_ref": f"projects/secrets-proj/secrets/profile-7-{_REQUEST_ID}",
        }
        assert "remote 34.1.2.3 1194" in secret_store[0].values[realization["secret_ref"]]

    def test_an_unhealthy_gateway_publishes_nothing(self, monkeypatch, secret_store):
        monkeypatch.setattr("vpn_access._probe_openvpn_gateway", lambda endpoint, port: False)
        session = raes_openvpn.prepare_raes_openvpn(_REQUEST_ID, 7, _plan(), _remote())

        with pytest.raises(ValueError, match="did not become ready"):
            session.publish(_gateway())
        assert not [ref for ref in secret_store[0].values if "profile" in ref]


class TestCleanup:
    def test_destroy_deletes_the_generations_credentials(self, secret_store):
        raes_openvpn.cleanup_raes_openvpn(_REQUEST_ID, 7, _remote())
        assert secret_store[0].deleted == [(7, UUID(_REQUEST_ID))]

    def test_a_range_without_remote_access_has_nothing_to_delete(self, secret_store):
        raes_openvpn.cleanup_raes_openvpn(_REQUEST_ID, 7, None)
        assert secret_store == []

    def test_teardown_plans_names_only(self):
        assert raes_openvpn.names_only_remote_access(_remote()) == RaesGceRemoteAccess(_TARGET, 5)
        assert raes_openvpn.names_only_remote_access(None) is None
