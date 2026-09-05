"""GCE warm-activation leaf-op composition tests (#28).

The scrub-before-realize ordering and fail-closed verification are proven in
``test_warm_activation`` with a fake port. These tests pin the concrete
:class:`raes_gcp_activate_gce.GceActivationOps` composition and the realize helper
with the GCE credential/VPN/apply primitives mocked at the boundary: the right
primitives are called with the right arguments, and negative verification fails
closed on any doubt. Live efficacy is proven on a real range (the repo norm).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from uuid import uuid4

import pytest
from shared.operation_results import ResultStep
from shared.raes.operation_input import RaesInputBindings, build_raes_operation_input
from shared.warm_pool.activation_input import (
    ActivationClaimant,
    ActivationGeneration,
    build_activation_input,
    parse_activation_input,
)

import raes_gcp_activate_realize
import raes_range_ops
from raes_gcp_activate_gce import GceActivationOps


def _activation():
    raes_input = build_raes_operation_input(
        plan={},
        bindings=RaesInputBindings(delivery=()),
        image_candidates={},
        range_backend="gce",
        instantiation_purpose="live_fire",
        legacy_range_id=1001,
    )
    payload = build_activation_input(
        claimant=ActivationClaimant(user_id=42, username="claimant@example.com", workspace_id=7),
        generation=ActivationGeneration(
            range_source="mission-control",
            instantiation_purpose="live_fire",
            range_backend="gce",
            legacy_range_id=1001,
            compatibility_digest="sha256:" + "a" * 64,
            prepared_generation_fence=str(uuid4()),
        ),
        raes_input=raes_input,
    )
    return parse_activation_input(payload)


class _FakeVpnOps:
    instances: ClassVar[list] = []

    def __init__(self):
        self.deleted: list = []
        self._present = _FakeVpnOps._present
        type(self).instances.append(self)

    def delete_generation(self, range_id, generation, *, delete_identity=True):
        self.deleted.append((range_id, generation, delete_identity))

    def issuer_present(self, range_id, generation):
        if isinstance(self._present, Exception):
            raise self._present
        return self._present


class TestScrubPreClaimAccess:
    def test_deletes_every_node_account_and_vpn_generation(self, monkeypatch):
        plan = SimpleNamespace(
            nodes=[SimpleNamespace(address="n1"), SimpleNamespace(address="n2")],
            accounts=[
                SimpleNamespace(target_address="n1", username="alice", auth_method="password"),
                # An account missing an auth method is skipped by the guard.
                SimpleNamespace(target_address="n2", username="bob", auth_method=""),
            ],
        )
        monkeypatch.setattr("raes_plan.parse_plan", lambda serialized: plan)
        ssh: list = []
        acct: list = []
        monkeypatch.setattr("gcp_guest_secrets.delete_raes_ssh_secret", lambda rid, key: ssh.append((rid, key)))
        monkeypatch.setattr(
            "gcp_guest_secrets.delete_raes_account_secret",
            lambda rid, key, username, auth_method: acct.append((rid, key, username, auth_method)),
        )
        _FakeVpnOps.instances = []
        _FakeVpnOps._present = False
        monkeypatch.setattr("vpn_secrets.GCPVpnSecretOps", _FakeVpnOps)

        prepared = uuid4()
        GceActivationOps.scrub_pre_claim_access(_activation(), prepared)

        assert ssh == [(1001, "n1"), (1001, "n2")]
        assert acct == [(1001, "n1", "alice", "password")]  # the empty-auth account is skipped
        assert _FakeVpnOps.instances[-1].deleted == [(1001, prepared, True)]


class TestRealizeClaimantAccess:
    def test_delegates_to_realize_helper(self, monkeypatch):
        members = [{"target_address": "n1", "channel": "ssh"}]
        seen: list = []
        monkeypatch.setattr(
            raes_gcp_activate_realize,
            "realize_claimant_access_on_cell",
            lambda activation, activate_generation: seen.append(activate_generation) or members,
        )
        gen = uuid4()
        assert GceActivationOps.realize_claimant_access(_activation(), gen) == members
        assert seen == [gen]


class TestPriorAccessRevoked:
    def test_true_when_issuer_absent(self, monkeypatch):
        _FakeVpnOps.instances = []
        _FakeVpnOps._present = False
        monkeypatch.setattr("vpn_secrets.GCPVpnSecretOps", _FakeVpnOps)
        prepared = uuid4()
        assert GceActivationOps.prior_access_revoked(_activation(), prepared) is True
        # It scrubs (belt-and-suspenders) then checks the issuer is absent.
        assert _FakeVpnOps.instances[-1].deleted == [(1001, prepared, True)]

    def test_false_when_issuer_still_present(self, monkeypatch):
        _FakeVpnOps.instances = []
        _FakeVpnOps._present = True
        monkeypatch.setattr("vpn_secrets.GCPVpnSecretOps", _FakeVpnOps)
        assert GceActivationOps.prior_access_revoked(_activation(), uuid4()) is False

    def test_false_when_probe_raises(self, monkeypatch):
        _FakeVpnOps.instances = []
        _FakeVpnOps._present = RuntimeError("secret store unreachable")
        monkeypatch.setattr("vpn_secrets.GCPVpnSecretOps", _FakeVpnOps)
        # Fail closed: a probe that cannot prove revocation returns False.
        assert GceActivationOps.prior_access_revoked(_activation(), uuid4()) is False


class TestRealizeClaimantAccessOnCell:
    def test_happy_path_returns_projected_members(self, monkeypatch):
        members = [{"target_address": "n1", "channel": "ssh"}]
        monkeypatch.setattr(raes_gcp_activate_realize, "parse_plan", lambda plan: SimpleNamespace())
        monkeypatch.setattr(raes_gcp_activate_realize, "_registry_resolver", lambda oi: lambda node: None)
        monkeypatch.setattr(
            raes_gcp_activate_realize,
            "realize_access_on_existing_cell",
            lambda *a, **k: {"n1": {"channel": "ssh"}},
        )
        monkeypatch.setattr(raes_gcp_activate_realize, "_realized_members", lambda result: members)
        assert raes_gcp_activate_realize.realize_claimant_access_on_cell(_activation(), uuid4()) == members

    def test_realization_error_fails_closed(self, monkeypatch):
        monkeypatch.setattr(raes_gcp_activate_realize, "parse_plan", lambda plan: SimpleNamespace())
        monkeypatch.setattr(raes_gcp_activate_realize, "_registry_resolver", lambda oi: lambda node: None)

        def _boom(*a, **k):
            raise RuntimeError("apply failed")

        monkeypatch.setattr(raes_gcp_activate_realize, "realize_access_on_existing_cell", _boom)
        with pytest.raises(raes_gcp_activate_realize.ActivationRealizationError):
            raes_gcp_activate_realize.realize_claimant_access_on_cell(_activation(), uuid4())


class _FakePlan:
    content: tuple = ()
    accounts: tuple = ()
    features: tuple = ()


def _patch_activate_orchestration(monkeypatch, *, reports, activation):
    op_id = str(uuid4())
    monkeypatch.setattr(raes_range_ops, "_require_generation", lambda rid, oid, op: (SimpleNamespace(), op_id))
    monkeypatch.setattr(
        raes_range_ops,
        "get_activation_operation_input",
        lambda generation, *, request_id: SimpleNamespace(request_id=request_id, operation_id=op_id, input=activation),
    )
    monkeypatch.setattr(raes_range_ops, "parse_plan", lambda plan: _FakePlan())
    monkeypatch.setattr(raes_range_ops, "snapshot_resources", lambda plan, verified: [])
    monkeypatch.setattr(raes_range_ops, "_report", lambda ref, operation, step, payload: reports.append(step))
    monkeypatch.setattr(
        raes_range_ops,
        "_report_failure",
        lambda ref, operation, diagnostic, reason_code=None: reports.append(("failure", reason_code)),
    )
    return op_id


class TestRunRaesRangeActivate:
    def test_happy_path_reports_running_snapshot_ready(self, monkeypatch):
        activation = _activation()
        reports: list = []
        _patch_activate_orchestration(monkeypatch, reports=reports, activation=activation)
        monkeypatch.setattr("raes_gcp_activate.default_activation_ops", lambda: object())
        monkeypatch.setattr(
            "raes_gcp_activate.activate_raes_range_cell",
            lambda **kwargs: SimpleNamespace(members=[{"target_address": "n1", "channel": "ssh"}]),
        )
        raes_range_ops.run_raes_range_activate("rid")
        assert reports == [
            ResultStep.RAES_ACTIVATE_RUNNING,
            ResultStep.RAES_ACTIVATE_SNAPSHOT,
            ResultStep.RAES_TERMINAL_READY,
        ]

    def test_input_read_failure_reports_and_raises(self, monkeypatch):
        reports: list = []
        monkeypatch.setattr(raes_range_ops, "_require_generation", lambda rid, oid, op: (SimpleNamespace(), "op"))

        def _boom(generation, *, request_id):
            raise RuntimeError("input unreadable")

        monkeypatch.setattr(raes_range_ops, "get_activation_operation_input", _boom)
        monkeypatch.setattr(
            raes_range_ops,
            "_report_failure",
            lambda ref, operation, diagnostic, reason_code=None: reports.append(("failure", reason_code)),
        )
        with pytest.raises(RuntimeError):
            raes_range_ops.run_raes_range_activate("rid")
        assert reports == [("failure", raes_range_ops._INPUT_REASON_CODE)]

    def test_activation_failure_reports_terminal_failure(self, monkeypatch):
        activation = _activation()
        reports: list = []
        _patch_activate_orchestration(monkeypatch, reports=reports, activation=activation)
        monkeypatch.setattr("raes_gcp_activate.default_activation_ops", lambda: object())

        def _boom(**kwargs):
            raise RuntimeError("activation blew up")

        monkeypatch.setattr("raes_gcp_activate.activate_raes_range_cell", _boom)
        with pytest.raises(RuntimeError):
            raes_range_ops.run_raes_range_activate("rid")
        assert reports[0] == ResultStep.RAES_ACTIVATE_RUNNING
        assert reports[-1][0] == "failure"


class _NotFound(Exception):
    pass


class TestIssuerPresent:
    def _ops(self, *, access):
        import vpn_secrets

        client = SimpleNamespace(access_secret_version=access)
        exceptions = SimpleNamespace(NotFound=_NotFound)
        return vpn_secrets.GCPVpnSecretOps(client=client, exceptions=exceptions, project_id="proj-1")

    def test_true_when_issuer_secret_resolves(self):
        ops = self._ops(access=lambda request: SimpleNamespace(payload=SimpleNamespace(data=b"issuer-material")))
        assert ops.issuer_present(1001, uuid4()) is True

    def test_false_when_issuer_secret_absent(self):
        def _raise(request):
            raise _NotFound

        ops = self._ops(access=_raise)
        assert ops.issuer_present(1001, uuid4()) is False


class TestGetActivationOperationInput:
    def test_returns_parsed_activation_run(self, monkeypatch):
        import provisioner_db_operation_input as dbin

        payload = build_activation_input(
            claimant=ActivationClaimant(user_id=42, username="claimant@example.com", workspace_id=7),
            generation=ActivationGeneration(
                range_source="mission-control",
                instantiation_purpose="live_fire",
                range_backend="gce",
                legacy_range_id=1001,
                compatibility_digest="sha256:" + "a" * 64,
                prepared_generation_fence=str(uuid4()),
            ),
            raes_input=_raw_raes_input(),
        )
        monkeypatch.setattr(
            dbin,
            "get_operation_input",
            lambda *, operation_id, request_id, resource, operation: SimpleNamespace(
                payload=payload, operation_id=operation_id, request_id=request_id
            ),
        )
        run = dbin.get_activation_operation_input("op-1", request_id="rid-1")
        assert run.operation_id == "op-1"
        assert run.request_id == "rid-1"
        assert run.input.claimant_user_id == 42

    def test_invalid_payload_raises_operation_input_error(self, monkeypatch):
        import provisioner_db_operation_input as dbin

        monkeypatch.setattr(
            dbin,
            "get_operation_input",
            lambda *, operation_id, request_id, resource, operation: SimpleNamespace(
                payload={"not": "a-valid-activation-payload"}, operation_id=operation_id, request_id=request_id
            ),
        )
        with pytest.raises(dbin.OperationInputError):
            dbin.get_activation_operation_input("op-1", request_id="rid-1")


def _raw_raes_input():
    return build_raes_operation_input(
        plan={},
        bindings=RaesInputBindings(delivery=()),
        image_candidates={},
        range_backend="gce",
        instantiation_purpose="live_fire",
        legacy_range_id=1001,
    )
