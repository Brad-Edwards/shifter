"""Pinned private OpenFGA deployment contract (#2315)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1]
IMAGE = "openfga/openfga@sha256:d53ce5c48413d01e75ecf375f3f74eb35c50f155fc028c41d03dbc7c9838fb38"


def _values() -> dict[str, object]:
    return {
        "openfga": {
            "enabled": True,
            "storeId": "01J00000000000000000000001",
            "modelId": "01J00000000000000000000000",
            "databaseCidrs": ["10.60.0.12/32"],
        }
    }


def _render(tmp_path: Path, provider_values: str, overrides: dict[str, object] | None = None):
    values = _values()
    if overrides:
        values["openfga"].update(overrides)  # type: ignore[union-attr]
    generated = tmp_path / "openfga.json"
    generated.write_text(json.dumps(values))
    return subprocess.run(
        [
            "helm",
            "template",
            "openfga-test",
            str(CHART),
            "-f",
            str(CHART / provider_values),
            "-f",
            str(generated),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("provider_values", ["values-aws-dev.yaml", "values-gcp-dev.yaml"])
def test_aws_and_gcp_render_the_same_private_server_contract(tmp_path, provider_values) -> None:
    result = _render(tmp_path, provider_values)
    assert result.returncode == 0, result.stderr
    documents = [item for item in yaml.safe_load_all(result.stdout) if item]
    by_identity = {(item["kind"], item["metadata"]["name"]): item for item in documents}

    deployment = by_identity["Deployment", "openfga"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item for item in container["env"]}
    assert container["image"] == IMAGE
    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
    }
    assert environment["OPENFGA_AUTHN_METHOD"]["value"] == "preshared"
    assert environment["OPENFGA_HTTP_TLS_ENABLED"]["value"] == "true"
    assert environment["OPENFGA_GRPC_ADDR"]["value"] == "127.0.0.1:8081"
    assert environment["OPENFGA_PLAYGROUND_ENABLED"]["value"] == "false"
    assert environment["OPENFGA_PROFILER_ENABLED"]["value"] == "false"
    assert environment["OPENFGA_DATASTORE_URI"]["valueFrom"]["secretKeyRef"]["key"] == "datastore-uri"
    assert by_identity["Service", "openfga"]["spec"]["type"] == "ClusterIP"
    assert ("Ingress", "openfga") not in by_identity

    migration = by_identity["Job", "openfga-datastore-migrate"]
    assert migration["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert migration["spec"]["template"]["spec"]["serviceAccountName"] == "openfga-admin"
    admin_service_account = by_identity["ServiceAccount", "openfga-admin"]
    assert admin_service_account["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert int(admin_service_account["metadata"]["annotations"]["helm.sh/hook-weight"]) < int(
        migration["metadata"]["annotations"]["helm.sh/hook-weight"]
    )
    assert "before-hook-creation" in admin_service_account["metadata"]["annotations"]["helm.sh/hook-delete-policy"]
    assert "hook-succeeded" not in admin_service_account["metadata"]["annotations"]["helm.sh/hook-delete-policy"]
    migration_policy = by_identity["NetworkPolicy", "allow-openfga-migration-egress"]
    policy_hooks = migration_policy["metadata"]["annotations"]
    assert policy_hooks["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert int(policy_hooks["helm.sh/hook-weight"]) < int(migration["metadata"]["annotations"]["helm.sh/hook-weight"])
    assert "hook-succeeded" not in policy_hooks["helm.sh/hook-delete-policy"]
    assert migration_policy["spec"]["egress"][0]["to"] == [{"ipBlock": {"cidr": "10.60.0.12/32"}}]
    assert by_identity["PodDisruptionBudget", "openfga"]["spec"]["minAvailable"] == 1

    portal = by_identity["Deployment", "portal-web"]["spec"]["template"]["spec"]
    portal_environment = {item["name"]: item for item in portal["containers"][0]["env"]}
    assert portal_environment["OPENFGA_API_TOKEN_FILE"]["value"] == "/run/secrets/openfga/token"
    assert portal_environment["OPENFGA_CA_CERT_PATH"]["value"] == "/run/secrets/openfga-ca/ca.crt"
    assert ("NetworkPolicy", "allow-portal-to-openfga") in by_identity
    assert ("NetworkPolicy", "allow-openfga-postgres-egress") in by_identity


@pytest.mark.parametrize(
    "overrides",
    [
        {"image": "openfga/openfga:v1.20.0"},
        {"databaseCidrs": []},
        {"storeId": "latest"},
    ],
)
def test_enabled_contract_rejects_unpinned_or_incomplete_values(tmp_path, overrides) -> None:
    result = _render(tmp_path, "values-aws-dev.yaml", overrides)
    assert result.returncode != 0
