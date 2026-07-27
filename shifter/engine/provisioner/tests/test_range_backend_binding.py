"""Range-backend ownership binding routing + legacy resolution (#1666).

Proves the destroy-after-selector-flip contract: teardown routes from the
per-operation binding resolved from persisted ownership, never the mutable
``GCP_RANGE_BACKEND`` env selector, and legacy (NULL-binding) ranges resolve from
durable ownership evidence or fail closed with a ``prerequisite`` diagnostic.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


def _range_cell_variables():
    """Build an admitted GCE VM range-cell request (mirrors test_range_terraform_runner)."""
    from shared.range_cells import build_gcp_vm_range_cell_request, build_scenario_artifact

    artifact = build_scenario_artifact(
        {
            "spec_schema": "range_spec",
            "spec_version": "1",
            "payload": {"scenario_id": "scenario-a", "user_id": 7, "subnets": []},
        }
    )
    return build_gcp_vm_range_cell_request(
        request_id="req-1",
        range_id=1,
        scenario_artifact=artifact,
        network_bindings=[],
    )


class TestDestroyRoutesFromBinding:
    """destroy_range routes from the explicit binding, overriding the env selector."""

    def test_routing_decision_follows_binding_not_env(self):
        """The gdc/gce routing predicates honor the binding, overriding the env selector.

        Asserts observable routing behavior of the pure predicates rather than
        mocking the first-party gdc destroy runners (ADR-019). A gdc-bound range
        selects the GDC plane even after the deploy selector flips to gce.
        """
        from range_terraform_runner import _uses_active_gdc_range_plane, _uses_gce_range_cells

        with patch.dict(os.environ, {"CLOUD_PROVIDER": "gcp", "GCP_RANGE_BACKEND": "gce"}, clear=True):
            # Binding gdc wins over the gce env selector (destroy-after-flip).
            assert _uses_active_gdc_range_plane("gdc") is True
            assert _uses_gce_range_cells("gdc") is False
            # The reverse binding also wins.
            assert _uses_gce_range_cells("gce") is True
            assert _uses_active_gdc_range_plane("gce") is False
            # With no binding (provision path) it still reads the env selector.
            assert _uses_gce_range_cells() is True

    def test_gce_binding_beats_gdc_env_selector(self):
        """A gce-bound range is torn down through the GCE range cell even if the env selector is gdc."""
        from range_terraform_runner import destroy_range

        variables = _range_cell_variables()
        calls = []
        with patch.dict(os.environ, {"CLOUD_PROVIDER": "gcp", "GCP_RANGE_BACKEND": "gdc"}, clear=True):
            destroy_range(
                "req-1",
                variables=variables,
                gce_destroy_range_cell=lambda rid, v: calls.append((rid, v)),
                backend="gce",
            )

        assert calls == [("req-1", variables)]

    def test_state_key_prefix_and_cleanup_follow_binding(self):
        from range_terraform_runner import get_range_state_key_prefix

        with patch.dict(os.environ, {"CLOUD_PROVIDER": "gcp", "GCP_RANGE_BACKEND": "gce"}, clear=True):
            assert get_range_state_key_prefix(backend="gdc") == "gcp/gdc-ranges"
            assert get_range_state_key_prefix(backend="gce") == "gcp/gce-range-cells"


class TestResolveLegacyRangeBackend:
    """resolve_legacy_range_backend infers the backend from durable asset evidence only."""

    def _conn_with_states(self, states):
        cur = MagicMock()
        cur.fetchall.return_value = [(s,) for s in states]
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        return conn

    @pytest.mark.parametrize(
        ("states", "expected"),
        [
            ([{"asset_type": "vm_runtime_vm"}, {"asset_type": "scenario_pod"}], "gdc"),
            ([{"asset_type": "gce_vm"}, {"asset_type": "gce_vm"}], "gce"),
            ([{"asset_type": "vm_runtime_vm"}, {"asset_type": "gce_vm"}], None),  # ambiguous
            ([], None),  # no instances
            ([{"asset_type": "unknown"}], None),  # unrecognized
        ],
    )
    def test_resolution_matrix(self, monkeypatch, states, expected):
        import range_backend_evidence

        monkeypatch.setattr(range_backend_evidence, "get_db_connection", lambda: self._conn_with_states(states))
        assert range_backend_evidence.resolve_legacy_range_backend("req-1") == expected


class TestResolveOperationBackend:
    """_resolve_operation_backend prefers the persisted binding and fails closed on legacy ambiguity."""

    def test_persisted_binding_wins_without_legacy_resolution(self, monkeypatch):
        import terraform_ops

        # A persisted binding returns immediately; the legacy resolver (a DB read)
        # must never be consulted, and neither must the env-selector path.
        monkeypatch.setattr(terraform_ops, "resolve_legacy_range_backend", MagicMock(side_effect=AssertionError))
        monkeypatch.setattr(terraform_ops, "resolve_cloud_provider", MagicMock(side_effect=AssertionError))
        result = terraform_ops._resolve_operation_backend({"range_backend": "gdc"}, "destroy")
        assert result == "gdc"

    def test_non_gcp_returns_none(self, monkeypatch):
        import terraform_ops

        monkeypatch.setattr(terraform_ops, "resolve_cloud_provider", lambda: "aws")
        assert terraform_ops._resolve_operation_backend({"range_backend": None}, "destroy") is None

    def test_legacy_destroy_without_evidence_fails_closed(self, monkeypatch):
        from shared.range_instantiation_policy import PREREQUISITE_DENIAL_CODE

        import terraform_ops
        from cloud.exceptions import CloudError

        monkeypatch.setattr(terraform_ops, "resolve_cloud_provider", lambda: "gcp")
        monkeypatch.setattr(terraform_ops, "resolve_legacy_range_backend", lambda rid: None)
        with pytest.raises(CloudError) as exc:
            terraform_ops._resolve_operation_backend({"range_backend": None, "request_id": "req-1"}, "destroy")
        assert exc.value.code == PREREQUISITE_DENIAL_CODE

    def test_legacy_destroy_resolves_from_evidence(self, monkeypatch):
        import terraform_ops

        monkeypatch.setattr(terraform_ops, "resolve_cloud_provider", lambda: "gcp")
        monkeypatch.setattr(terraform_ops, "resolve_legacy_range_backend", lambda rid: "gdc")
        result = terraform_ops._resolve_operation_backend({"range_backend": None, "request_id": "req-1"}, "destroy")
        assert result == "gdc"

    def test_gcp_provision_with_null_binding_fails_closed(self, monkeypatch):
        """A normal GCP provision must consume the persisted admission binding."""
        from shared.range_instantiation_policy import PREREQUISITE_DENIAL_CODE

        import terraform_ops
        from cloud.exceptions import CloudError

        monkeypatch.setattr(terraform_ops, "resolve_cloud_provider", lambda: "gcp")
        monkeypatch.setattr(terraform_ops, "resolve_legacy_range_backend", MagicMock(side_effect=AssertionError))
        with pytest.raises(CloudError) as exc:
            terraform_ops._resolve_operation_backend({"range_backend": None}, "up")
        assert exc.value.code == PREREQUISITE_DENIAL_CODE


class TestProvisionRoutesFromBinding:
    """Provision-time validation and dispatch consume one persisted binding."""

    def test_gce_binding_validates_artifact_after_selector_flips_to_gdc(self, monkeypatch):
        from shared.range_cells import build_scenario_artifact

        import terraform_ops

        artifact = build_scenario_artifact(
            {
                "spec_schema": "range_spec",
                "spec_version": "1",
                "payload": {"scenario_id": "scenario-a", "user_id": 7, "subnets": []},
            }
        )
        dispatch = MagicMock()
        monkeypatch.setattr(
            terraform_ops,
            "get_range_data_by_request_id",
            MagicMock(
                return_value={
                    "request_id": "req-1",
                    "range_id": 1,
                    "user_id": 7,
                    "spec": artifact["payload"],
                    "spec_envelope": artifact,
                    "range_backend": "gce",
                    "instantiation_purpose": "live_fire",
                }
            ),
        )
        monkeypatch.setattr(terraform_ops, "_dispatch_terraform_operation", dispatch)

        with patch.dict(os.environ, {"CLOUD_PROVIDER": "gcp", "GCP_RANGE_BACKEND": "gdc"}, clear=True):
            terraform_ops.run_range_terraform("up", "req-1")

        operation = dispatch.call_args.args[1]
        assert operation.backend == "gce"
        assert operation.purpose.value == "live_fire"
        assert operation.scenario_artifact == artifact

    def test_gce_binding_requires_live_fire_purpose_before_dispatch(self, monkeypatch):
        import terraform_ops
        from cloud.exceptions import CloudError

        dispatch = MagicMock()
        monkeypatch.setattr(
            terraform_ops,
            "get_range_data_by_request_id",
            MagicMock(
                return_value={
                    "request_id": "req-1",
                    "range_id": 1,
                    "user_id": 7,
                    "spec": {},
                    "range_backend": "gce",
                    "instantiation_purpose": None,
                }
            ),
        )
        monkeypatch.setattr(terraform_ops, "_dispatch_terraform_operation", dispatch)
        publish_failed = MagicMock()
        monkeypatch.setattr(terraform_ops, "publish_failed", publish_failed)

        with pytest.raises(CloudError) as exc:
            terraform_ops.run_range_terraform("up", "req-1")

        assert getattr(exc.value, "code", None) == "prerequisite"
        dispatch.assert_not_called()
        publish_failed.assert_called_once()

    def test_unsupported_composition_fails_before_ngfw_or_dispatch(self, monkeypatch):
        from shared.range_cells import build_scenario_artifact
        from shared.range_instantiation_policy import UNSUPPORTED_CAPABILITY_CODE

        import terraform_ops
        from cloud.exceptions import CloudError

        artifact = build_scenario_artifact(
            {
                "spec_schema": "range_spec",
                "spec_version": "1",
                "payload": {
                    "scenario_id": "ngfw-scenario",
                    "user_id": 7,
                    "ngfw": True,
                    "subnets": [],
                },
            }
        )
        dispatch = MagicMock()
        ensure_ngfw = MagicMock()
        monkeypatch.setattr(
            terraform_ops,
            "get_range_data_by_request_id",
            MagicMock(
                return_value={
                    "request_id": "req-1",
                    "range_id": 1,
                    "user_id": 7,
                    "spec": artifact["payload"],
                    "spec_envelope": artifact,
                    "range_backend": "gce",
                    "instantiation_purpose": "live_fire",
                }
            ),
        )
        monkeypatch.setattr(terraform_ops, "_ensure_ngfw_ready_for_provisioning", ensure_ngfw)
        monkeypatch.setattr(terraform_ops, "_dispatch_terraform_operation", dispatch)
        monkeypatch.setattr(terraform_ops, "publish_failed", MagicMock())

        with pytest.raises(CloudError) as exc:
            terraform_ops.run_range_terraform("up", "req-1")

        assert exc.value.code == UNSUPPORTED_CAPABILITY_CODE
        ensure_ngfw.assert_not_called()
        dispatch.assert_not_called()

    def test_provision_pipeline_threads_binding_through_every_backend_seam(self, monkeypatch):
        from shared.range_instantiation_policy import InstantiationPurpose

        import terraform_ops

        allocate = MagicMock(return_value=[])
        build_variables = MagicMock(return_value=_range_cell_variables())
        apply = MagicMock(return_value={"subnets": {}, "instances": []})
        monkeypatch.setattr(terraform_ops, "publish_status_update", MagicMock())
        monkeypatch.setattr(terraform_ops, "_allocate_range_subnet_cidrs", allocate)
        monkeypatch.setattr(terraform_ops, "_build_operation_variables", build_variables)
        monkeypatch.setattr(terraform_ops.range_terraform_runner, "apply_range", apply)
        monkeypatch.setattr(terraform_ops, "_validate_provisioned_outputs", MagicMock())
        monkeypatch.setattr(terraform_ops, "_validate_ngfw_range_attachment", MagicMock())
        monkeypatch.setattr(terraform_ops, "_configure_ngfw_for_range", MagicMock())
        monkeypatch.setattr(terraform_ops, "run_instance_setup", MagicMock())
        monkeypatch.setattr(
            terraform_ops,
            "get_range_data_by_request_id",
            MagicMock(return_value={"ngfw_instance_id": None}),
        )
        monkeypatch.setattr(terraform_ops, "write_provisioned_state", MagicMock())
        monkeypatch.setattr(terraform_ops, "publish_ready", MagicMock())
        operation = terraform_ops.RangeOperation(
            request_id="req-1",
            range_id=1,
            user_id=7,
            range_spec={"subnets": []},
            scenario_artifact={"digest": "bound"},
            backend="gce",
            purpose=InstantiationPurpose.LIVE_FIRE,
        )

        terraform_ops._run_terraform_provision(operation)

        assert allocate.call_args.kwargs["persist_to_scenario"] is False
        assert build_variables.call_args.kwargs["backend"] == "gce"
        apply.assert_called_once_with(
            "req-1",
            build_variables.return_value,
            backend="gce",
            purpose=InstantiationPurpose.LIVE_FIRE,
        )
