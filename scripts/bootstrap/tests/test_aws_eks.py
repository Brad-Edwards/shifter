"""Behavioral tests for the explicit AWS EKS/Helm bundle lifecycle."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import aws_eks


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        backend="aws",
        deployment=SimpleNamespace(name="shifter", domain="shifter.example.com", profile="dev"),
        settings={"region": "us-east-2"},
        secrets={
            "django_secret_key": "shifter/dev/app",
            "db_password": "shifter/dev/db",
        },
    )


def _terraform_outputs() -> dict[str, object]:
    return {
        "cluster_name": {"value": "shifter-dev-eks"},
        "cluster_access_role_arn": {"value": "arn:aws:iam::123456789012:role/shifter-dev-eks-deployer"},
        "certificate_arn": {"value": "arn:aws:acm:us-east-2:123456789012:certificate/example"},
        "waf_acl_arn": {"value": "arn:aws:wafv2:us-east-2:123456789012:regional/webacl/example/id"},
        "workload_role_arns": {
            "value": {
                "portal": "arn:aws:iam::123456789012:role/shifter-dev-portal",
                "workers": "arn:aws:iam::123456789012:role/shifter-dev-workers",
                "ctfScheduler": "arn:aws:iam::123456789012:role/shifter-dev-ctf-scheduler",
                "ingress": "arn:aws:iam::123456789012:role/shifter-dev-ingress",
            }
        },
        "runtime_env": {
            "value": {
                "AWS_REGION": "us-east-2",
                "ENGINE_TASK_CLUSTER": "arn:aws:ecs:us-east-2:123456789012:cluster/shifter-dev",
                "ENGINE_TASK_DEFINITION": (
                    "arn:aws:ecs:us-east-2:123456789012:task-definition/shifter-dev-provisioner:1"
                ),
                "ENGINE_TASK_NETWORK_SECURITY_GROUP_ID": "sg-0123456789abcdef0",
                "ENGINE_TASK_NETWORK_SUBNET_IDS": "subnet-private-a,subnet-private-b",
                "OIDC_AUTH_DOMAIN": "https://shifter-dev.auth.us-east-2.amazoncognito.com",
                "OIDC_ISSUER_URL": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_example",
                "OIDC_RP_CLIENT_ID": "example-client-id",
                "OIDC_SECRET_ID": "shifter/dev/cognito",
                "QUEUE_CMS_CONSUMER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/cms",
                "QUEUE_CMS_PUBLISHER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/cms",
                "QUEUE_ENGINE_CONSUMER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/engine",
                "QUEUE_ENGINE_PUBLISHER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/engine",
                "QUEUE_MC_CONSUMER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/mc",
                "QUEUE_MC_PUBLISHER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/mc",
                "RANGE_EVENTS_TOPIC_ID": "arn:aws:sns:us-east-2:123456789012:range-events",
                "STORAGE_BUCKET_NAME": "shifter-dev-storage",
            }
        },
        "ingress_source_cidrs": {"value": ["10.42.0.0/16"]},
        "provider_api_cidrs": {"value": ["10.42.0.0/16"]},
        "private_service_cidrs": {"value": ["10.42.0.0/16"]},
        "kubernetes_api_cidrs": {"value": ["172.20.0.0/16"]},
    }


def _images() -> dict[str, str]:
    digest = "a" * 64
    return {
        "platform": f"123456789012.dkr.ecr.us-east-2.amazonaws.com/shifter/platform@sha256:{digest}",
        "guacd": f"123456789012.dkr.ecr.us-east-2.amazonaws.com/shifter/guacd@sha256:{digest}",
        "guacamoleClient": (f"123456789012.dkr.ecr.us-east-2.amazonaws.com/shifter/guacamole-client@sha256:{digest}"),
    }


def test_render_values_is_non_secret_backend_neutral_and_digest_pinned():
    values = aws_eks.render_aws_values(_config(), _terraform_outputs(), _images())

    assert values["provider"]["name"] == "aws"
    assert values["capabilities"]["kubernetesJobLauncher"] is False
    assert values["edge"]["hostname"] == "shifter.example.com"
    assert values["edge"]["ingress"]["className"] == "alb"
    assert values["edge"]["ingress"]["annotations"]["alb.ingress.kubernetes.io/certificate-arn"].endswith(
        "certificate/example"
    )
    assert values["network"]["ingressSourceCidrs"] == ["10.42.0.0/16"]
    assert values["network"]["kubernetesApiCidrs"] == ["172.20.0.0/16"]
    assert values["identity"]["serviceAccountRoleArns"]["portal"].endswith("shifter-dev-portal")
    assert values["identity"]["serviceAccountRoleArns"]["workers"].endswith("shifter-dev-workers")
    assert values["identity"]["serviceAccountRoleArns"]["ctfScheduler"].endswith("shifter-dev-ctf-scheduler")
    assert values["runtimeEnv"]["CLOUD_PROVIDER"] == "aws"
    assert values["runtimeEnv"]["ENVIRONMENT"] == "development"
    assert values["runtimeEnv"]["AUTH_PROVIDER"] == "oidc"
    assert values["runtimeEnv"]["ENGINE_TASK_CLUSTER"].endswith("cluster/shifter-dev")
    assert values["runtimeEnv"]["QUEUE_ENGINE_CONSUMER_ID"].endswith("/engine")
    assert values["runtimeEnv"]["OIDC_SECRET_ID"] == "shifter/dev/cognito"
    assert values["runtime"]["secretReferences"] == {
        "app": "shifter/dev/app",
        "database": "shifter/dev/db",
    }
    rendered = json.dumps(values)
    assert "@sha256:" in rendered
    assert "SECRET_KEY=" not in rendered
    assert "PASSWORD=" not in rendered


def test_render_values_rejects_incomplete_runtime_contract():
    outputs = _terraform_outputs()
    outputs["runtime_env"]["value"].pop("ENGINE_TASK_CLUSTER")

    with pytest.raises(ValueError, match="ENGINE_TASK_CLUSTER"):
        aws_eks.render_aws_values(_config(), outputs, _images())


@pytest.mark.parametrize(
    "image",
    [
        "repo/platform:latest",
        "repo/platform@sha256:short",
        "repo/platform@sha256:" + ("g" * 64),
        "repo/platform@sha256:" + ("a" * 64) + ":tag",
    ],
)
def test_render_values_rejects_non_attested_image_identities(image):
    images = _images()
    images["platform"] = image

    with pytest.raises(ValueError, match="repository@sha256"):
        aws_eks.render_aws_values(_config(), _terraform_outputs(), images)


def test_deploy_sequence_uses_saved_plan_bounded_access_and_atomic_helm(tmp_path, monkeypatch):
    config_path = tmp_path / "shifter.yaml"
    config_path.write_text("placeholder")
    image_path = tmp_path / "images.json"
    image_path.write_text(json.dumps(_images()))
    backend_config_path = tmp_path / "dev.s3.tfbackend"
    backend_config_path.write_text('bucket = "state"\n')
    terraform_inputs_path = tmp_path / "eks.tfvars.json"
    terraform_inputs_path.write_text(
        json.dumps(
            {
                "aws_region": "us-east-2",
                "deployment_role_arn": "arn:aws:iam::123456789012:role/shifter-dev-deployer",
                "domain_name": "shifter.example.com",
                "ingress_source_cidrs": ["10.42.0.0/16"],
                "load_balancer_controller_policy_arn": (
                    "arn:aws:iam::123456789012:policy/shifter-dev-load-balancer-controller"
                ),
                "provider_api_cidrs": ["10.42.0.0/16"],
                "runtime_env": _terraform_outputs()["runtime_env"]["value"],
            }
        )
    )
    calls: list[list[str]] = []
    secret_stdin_calls: list[tuple[list[str], str]] = []
    result = SimpleNamespace(stdout=json.dumps(_terraform_outputs()))
    runner = Mock(side_effect=lambda cmd, **kwargs: calls.append(cmd) or result)
    monkeypatch.setattr(aws_eks, "load_root_config", lambda _path: _config())
    monkeypatch.setattr(aws_eks, "run_cmd", runner)
    monkeypatch.setattr(
        aws_eks,
        "run_cmd_secret_stdin",
        lambda cmd, *, secret_stdin: secret_stdin_calls.append((cmd, secret_stdin)) or 0,
    )
    monkeypatch.setattr(aws_eks, "preflight_gate", Mock())

    evidence = aws_eks.deploy_eks(
        config_path,
        image_path,
        backend_config_path=backend_config_path,
        terraform_inputs_path=terraform_inputs_path,
        aws_profile="operator",
        dry_run=False,
    )

    terraform_root = str(aws_eks.eks_root("dev"))
    assert [
        "terraform",
        f"-chdir={terraform_root}",
        "init",
        "-input=false",
        "-reconfigure",
        f"-backend-config={backend_config_path}",
    ] in calls
    assert [
        "terraform",
        f"-chdir={terraform_root}",
        "plan",
        "-input=false",
        f"-var-file={terraform_inputs_path}",
        "-out=shifter-eks.tfplan",
    ] in calls
    assert ["terraform", f"-chdir={terraform_root}", "apply", "shifter-eks.tfplan"] in calls
    assert any(cmd[:3] == ["aws", "eks", "update-kubeconfig"] and "--role-arn" in cmd for cmd in calls)
    assert sum(cmd[:3] == ["kubectl", "apply", "-f"] for cmd in calls) == 2
    assert any(
        cmd[:4] == ["helm", "upgrade", "--install", "aws-load-balancer-controller"]
        and "--version" in cmd
        and "3.2.2" in cmd
        for cmd in calls
    )
    assert [
        "kubectl",
        "rollout",
        "status",
        "deployment/aws-load-balancer-controller",
        "--namespace",
        "kube-system",
        "--timeout=5m",
    ] in calls
    assert any(
        cmd[:4] == ["helm", "upgrade", "--install", "shifter"] and {"--atomic", "--wait", "--values", "-"} <= set(cmd)
        for cmd, _values in secret_stdin_calls
    )
    assert len(secret_stdin_calls) == 3
    assert all('"secretReferences"' in values for _cmd, values in secret_stdin_calls)
    assert not any("shifter/dev/app" in token for call in calls for token in call)
    assert not any("destroy" in cmd for cmd in calls)
    assert evidence["backend"] == "aws"
    assert evidence["profile"] == "dev"
    assert evidence["health_url"] == "https://shifter.example.com/health/"


def test_teardown_is_scoped_to_the_eks_root_and_fails_without_valid_aws_config(tmp_path, monkeypatch):
    config_path = tmp_path / "shifter.yaml"
    config_path.write_text("placeholder")
    calls: list[list[str]] = []
    monkeypatch.setattr(aws_eks, "load_root_config", lambda _path: _config())
    monkeypatch.setattr(aws_eks, "run_cmd", lambda cmd, **kwargs: calls.append(cmd))

    backend_config_path = tmp_path / "dev.s3.tfbackend"
    backend_config_path.write_text('bucket = "state"\n')
    terraform_inputs_path = tmp_path / "eks.tfvars.json"
    terraform_inputs_path.write_text("{}")

    aws_eks.teardown_eks(
        config_path,
        backend_config_path=backend_config_path,
        terraform_inputs_path=terraform_inputs_path,
        aws_profile="operator",
        dry_run=False,
    )

    root = str(aws_eks.eks_root("dev"))
    assert calls[0][:4] == ["helm", "uninstall", "shifter", "--namespace"]
    assert [
        "terraform",
        f"-chdir={root}",
        "init",
        "-input=false",
        "-reconfigure",
        f"-backend-config={backend_config_path}",
    ] in calls
    assert [
        "terraform",
        f"-chdir={root}",
        "destroy",
        "-auto-approve",
        f"-var-file={terraform_inputs_path}",
    ] in calls
    assert not any("/portal" in token or "/range" in token for call in calls for token in call)


def test_cli_exposes_explicit_eks_commands_without_branch_inputs(monkeypatch):
    import cli

    parser = cli._build_parser()
    deploy = parser.parse_args(
        ["eks-deploy", "--config", "shifter.yaml", "--images", "images.json", "--profile", "operator"]
    )
    teardown = parser.parse_args(["eks-teardown", "--config", "shifter.yaml", "--profile", "operator"])

    assert deploy.command == "eks-deploy"
    assert teardown.command == "eks-teardown"
    assert not hasattr(deploy, "branch")
    assert not hasattr(teardown, "ref")


def test_aws_eks_module_import_does_not_depend_on_caller_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(Path(aws_eks.__file__).parent))
    sys.modules.pop("aws_eks", None)
    __import__("aws_eks")
