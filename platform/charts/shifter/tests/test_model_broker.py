"""Evaluate the rendered broker's credential and additive network boundaries."""

from __future__ import annotations

import ipaddress
import json
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1]


def enabled_values():
    return {
        "provider": {"name": "gcp"},
        "modelBroker": {
            "enabled": True,
            "hostname": "models.example.test",
            "vip": "10.40.0.25",
            "admitted_subnets": ["10.50.1.0/24"],
            "global_access": False,
            "tls_secret_name": "model-broker-tls-v1",
            "control_tls_secret_name": "model-control-tls-v1",
            "trust_configmap_name": "model-access-ca-v1",
            "gsa": "model-broker@platform-example.iam.gserviceaccount.com",
            "region": "us-central1",
            "catalog_json": (
                _catalog := (
                    CHART.parents[2]
                    / "docs/architecture/model-access/example-policy.v1.json"
                ).read_text()
            ),
            "identities_json": "{}",
            "catalog_digest": json.loads(_catalog)["digest"],
            "control_env": {
                "MODEL_ACCESS_ENABLED": "false",
                "MODEL_ACCESS_CATALOG_PATH": "/etc/shifter/model-access/catalog.json",
                "MODEL_ACCESS_CATALOG_DIGEST": json.loads(_catalog)["digest"],
            },
        },
        "network": {
            "providerApiCidrs": ["199.36.153.8/30"],
            "privateServiceCidrs": ["10.60.0.0/24"],
        },
        "runtimeEnv": {
            "DB_SECRET_ID": "portal-secret-sentinel",
            "DJANGO_SETTINGS_MODULE": "config.settings",
        },
    }


def render(tmp_path, values):
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    return subprocess.run(
        ["helm", "template", "broker-test", str(CHART), "-f", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )


def aws_values():
    """Synthetic regional deployment with Terraform-owned private NLB targets."""
    values = enabled_values()
    values["provider"]["name"] = "aws"
    values["modelBroker"].update(
        {
            "gsa": "",
            "vip": "",
            "region": "us-east-2",
            "role_arn": "arn:aws:iam::123456789012:role/model-broker",
            "provisioner_subject": "arn:aws:iam::123456789012:role/provisioner",
            "target_group_arn": "arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/models/0123456789abcdef",
            "vpc_id": "vpc-" + "1" * 17,
            "endpoint_cidrs": ["10.42.0.10/32", "10.42.0.11/32"],
            "health_check_cidrs": ["10.42.0.0/20"],
        }
    )
    # Other applications may reach broad API destinations; broker must remain
    # isolated even when these additive policies are present.
    values["network"]["providerApiCidrs"] = ["0.0.0.0/0"]
    return values


def test_aws_broker_uses_only_private_endpoints_and_exact_irsa(tmp_path):
    result = render(tmp_path, aws_values())
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    by_name = {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs}
    pod = by_name["Deployment", "model-broker"]["spec"]["template"]
    env = {item["name"]: item["value"] for item in pod["spec"]["containers"][0]["env"]}
    assert env["MODEL_BROKER_PROVIDER"] == "aws"
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert env["AWS_STS_REGIONAL_ENDPOINTS"] == "regional"
    assert "envFrom" not in pod["spec"]["containers"][0]
    account = by_name["ServiceAccount", "model-broker"]
    assert account["metadata"]["annotations"] == {
        "eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/model-broker",
        "eks.amazonaws.com/sts-regional-endpoints": "true",
    }
    assert account["automountServiceAccountToken"] is False
    service = by_name["Service", "model-broker"]["spec"]
    assert service["type"] == "ClusterIP"
    binding = by_name["TargetGroupBinding", "model-broker"]["spec"]
    assert binding["targetType"] == "ip"
    assert binding["serviceRef"] == {"name": "model-broker", "port": 443}
    control = by_name["Deployment", "model-access-control"]["spec"]["template"]["spec"][
        "containers"
    ][0]
    control_env = {item["name"]: item["value"] for item in control["env"]}
    assert control_env["MODEL_CONTROL_REGION"] == "us-east-2"
    assert control_env["MODEL_CONTROL_PROVIDER"] == "aws"
    assert control_env["MODEL_CONTROL_BROKER_SUBJECT"] == env.get(
        "AWS_ROLE_ARN", account["metadata"]["annotations"]["eks.amazonaws.com/role-arn"]
    )
    policies = [
        doc["spec"]
        for doc in docs
        if doc["kind"] == "NetworkPolicy"
        and doc["metadata"]["namespace"] == "shifter-platform"
        and selected(doc["spec"]["podSelector"], pod["metadata"]["labels"])
    ]
    egress = [rule for policy in policies for rule in policy.get("egress", [])]
    assert {port["port"] for rule in egress for port in rule["ports"]} == {
        53,
        443,
        8444,
    }
    assert {
        peer["ipBlock"]["cidr"]
        for rule in egress
        for peer in rule["to"]
        if "ipBlock" in peer
    } == {"10.42.0.10/32", "10.42.0.11/32"}
    assert "portal-secret-sentinel" not in json.dumps(pod)


@pytest.mark.parametrize(
    "fault",
    ["role_arn", "target_group_arn", "endpoint_cidrs", "health_check_cidrs", "gsa"],
)
def test_aws_broker_rejects_incomplete_or_mixed_cloud_boundary(tmp_path, fault):
    values = aws_values()
    values["modelBroker"][fault] = (
        "wrong@project-example.iam.gserviceaccount.com"
        if fault == "gsa"
        else []
        if fault.endswith("cidrs")
        else ""
    )
    assert render(tmp_path, values).returncode != 0


def selected(selector, labels):
    if any(
        labels.get(key) != value
        for key, value in selector.get("matchLabels", {}).items()
    ):
        return False
    for expression in selector.get("matchExpressions", []):
        value = labels.get(expression["key"])
        if expression["operator"] == "NotIn" and value in expression["values"]:
            return False
        if expression["operator"] == "In" and value not in expression["values"]:
            return False
    return True


def test_broker_has_isolated_process_and_effective_network_policy(tmp_path):
    result = render(tmp_path, enabled_values())
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    deployment = next(
        doc
        for doc in docs
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "model-broker"
    )
    pod = deployment["spec"]["template"]
    container = pod["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "model_broker"]
    assert "envFrom" not in container
    assert all(
        "DB_" not in item["name"] and "SECRET_ID" not in item["name"]
        for item in container["env"]
    )
    assert pod["spec"]["serviceAccountName"] == "model-broker"
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert "portal-secret-sentinel" not in json.dumps(deployment)
    policies = [
        doc["spec"]
        for doc in docs
        if doc["kind"] == "NetworkPolicy"
        and doc["metadata"]["namespace"] == "shifter-platform"
        and selected(doc["spec"]["podSelector"], pod["metadata"]["labels"])
    ]
    ingress = [rule for policy in policies for rule in policy.get("ingress", [])]
    assert ingress == [
        {
            "from": [{"ipBlock": {"cidr": "10.50.1.0/24"}}],
            "ports": [{"protocol": "TCP", "port": 8443}],
        }
    ]
    for source, admitted in [("10.50.1.10", True), ("10.50.2.10", False)]:
        assert (
            any(
                ipaddress.ip_address(source)
                in ipaddress.ip_network(peer["ipBlock"]["cidr"])
                for rule in ingress
                for peer in rule["from"]
            )
            is admitted
        )
    allowed_ports = {
        port["port"]
        for policy in policies
        for rule in policy.get("egress", [])
        for port in rule.get("ports", [])
    }
    assert not {5432, 6378, 6379, 8000, 6443, 6444} & allowed_ports
    assert {443, 8444, 53, 80} <= allowed_ports
    service = next(
        doc
        for doc in docs
        if doc["kind"] == "Service" and doc["metadata"]["name"] == "model-broker"
    )
    assert service["spec"]["externalTrafficPolicy"] == "Local"
    assert service["spec"]["loadBalancerIP"] == "10.40.0.25"
    assert service["spec"]["loadBalancerSourceRanges"] == ["10.50.1.0/24"]
    assert service["spec"]["ports"] == [
        {"name": "https", "port": 443, "targetPort": "https", "protocol": "TCP"}
    ]


@pytest.mark.parametrize(
    "mutation", ["provider", "network", "image", "extra_env", "missing_tls"]
)
def test_broker_render_rejects_unsafe_boundary(tmp_path, mutation):
    values = enabled_values()
    if mutation == "provider":
        values["provider"]["name"] = "aws"
    elif mutation == "network":
        values["network"]["enabled"] = False
    elif mutation == "image":
        values["images"] = {"platform": "registry.example/platform:latest"}
    elif mutation == "extra_env":
        values["modelBroker"]["env"] = {"DB_PASSWORD": "sentinel"}
    else:
        values["modelBroker"]["tls_secret_name"] = ""
    assert render(tmp_path, values).returncode != 0


def test_disabled_render_keeps_draining_broker_excluded_from_application_egress(
    tmp_path,
):
    values = enabled_values()
    values["modelBroker"] = {"enabled": False}
    result = render(tmp_path, values)
    assert result.returncode == 0, result.stderr
    policies = {
        doc["metadata"]["name"]: doc["spec"]
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "NetworkPolicy"
    }
    labels = {"app.kubernetes.io/component": "model-broker"}
    for name in (
        "allow-platform-provider-apis-egress",
        "allow-platform-private-service-egress",
    ):
        assert not selected(policies[name]["podSelector"], labels)


def test_both_authorization_path_deployments_have_disruption_budgets(tmp_path):
    result = render(tmp_path, enabled_values())
    assert result.returncode == 0, result.stderr
    budgets = {
        doc["metadata"]["name"]: doc["spec"]
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "PodDisruptionBudget"
    }
    for name in ("model-broker", "model-access-control"):
        assert budgets[name]["minAvailable"] == 1
        assert budgets[name]["unhealthyPodEvictionPolicy"] == "AlwaysAllow"
        assert (
            budgets[name]["selector"]["matchLabels"]["app.kubernetes.io/component"]
            == name
        )


def test_configmap_transport_rejects_oversize_identity_plus_catalog_payload(tmp_path):
    values = enabled_values()
    values["modelBroker"]["identities_json"] = json.dumps(
        {"padding": "x" * (96 * 1024)}
    )
    result = render(tmp_path, values)
    assert result.returncode != 0
    assert "ConfigMap transport" in result.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("admitted_subnets", ["0.0.0.0/0"]),
        ("admitted_subnets", ["172.16.0.0/8"]),
        ("admitted_subnets", ["10.999.0.0/24"]),
        ("vip", "10.400.0.1"),
        ("control_tls_secret_name", "model-broker-tls-v1"),
    ],
)
def test_direct_helm_transport_rejects_invalid_network_or_shared_tls(
    tmp_path, field, value
):
    values = enabled_values()
    values["modelBroker"][field] = value
    assert render(tmp_path, values).returncode != 0


@pytest.mark.parametrize(
    "mutation,error_details",
    [
        ("missing", ("enabled model broker requires control_env",)),
        ("digest", ("model control environment must bind the mounted catalog digest",)),
        (
            "path",
            ("MODEL_ACCESS_CATALOG_PATH", "/etc/shifter/model-access/catalog.json"),
        ),
        ("unknown", ("DB_PASSWORD", "not allowed")),
        ("disabled_catalog", ("enabled model access requires an enabled catalog",)),
    ],
)
def test_control_environment_rejects_unbound_catalog(tmp_path, mutation, error_details):
    values = enabled_values()
    env = values["modelBroker"]["control_env"]
    if mutation == "missing":
        env.clear()
    elif mutation == "digest":
        env["MODEL_ACCESS_CATALOG_DIGEST"] = "sha256:" + "0" * 64
    elif mutation == "path":
        env["MODEL_ACCESS_CATALOG_PATH"] = "/tmp/unmounted.json"
    elif mutation == "unknown":
        env["DB_PASSWORD"] = "synthetic-sentinel"
    else:
        env["MODEL_ACCESS_ENABLED"] = "true"
    result = render(tmp_path, values)
    assert result.returncode != 0
    for detail in error_details:
        assert detail in result.stderr
