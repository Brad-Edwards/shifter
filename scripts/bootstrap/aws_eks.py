"""Explicit AWS EKS lifecycle owner for the root-configured AWS bundle.

The platform control plane is Terraform-owned infrastructure plus the single
provider-neutral Helm chart. ECS remains a separate range-task transport and is
never read, imported, adopted, or destroyed by this module.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bootstrap_core import get_repo_root, run_cmd
from preflight import Cloud, Mode, preflight_gate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIFTER_PACKAGE_ROOT = _REPO_ROOT / "shifter"
if str(_SHIFTER_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SHIFTER_PACKAGE_ROOT))

from installation.loader import load_root_config  # noqa: E402
from installation.runtime_inventory import AWS_EKS_REQUIRED_RUNTIME_ENV_KEYS  # noqa: E402

_IMAGE_IDENTITY_RE = re.compile(r"^[^\s:@]+(?:[/:][^\s:@]+)*@sha256:[0-9a-f]{64}$")
_EKS_NAMESPACE = "shifter-system"
_HELM_RELEASE = "shifter"
_LOAD_BALANCER_CONTROLLER_CHART_VERSION = "3.2.2"
_PLATFORM_NAMESPACES = {
    "shifter-platform": "control",
    "shifter-jobs": "jobs",
}
_WORKLOAD_ROLE_KEYS = frozenset({"portal", "workers", "ctfScheduler"})
_RENDERER_OWNED_RUNTIME_ENV = frozenset(
    {
        "AUTH_PROVIDER",
        "CLOUD_PROVIDER",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "ENVIRONMENT",
        "SITE_URL",
    }
)
_REQUIRED_TERRAFORM_INPUTS = frozenset(
    {
        "aws_region",
        "deployment_role_arn",
        "domain_name",
        "ingress_source_cidrs",
        "load_balancer_controller_policy_arn",
        "provider_api_cidrs",
        "runtime_env",
    }
)


def eks_root(profile: str) -> Path:
    """Return the isolated Terraform root for an EKS profile."""
    if profile not in {"dev", "proof", "prod"}:
        raise ValueError(f"unsupported AWS EKS profile {profile!r}")
    return get_repo_root() / "platform" / "terraform" / "environments" / profile / "eks"


def _output(outputs: Mapping[str, object], name: str) -> Any:
    raw = outputs.get(name)
    if not isinstance(raw, Mapping) or "value" not in raw:
        raise ValueError(f"missing required EKS Terraform output {name!r}")
    return raw["value"]


def _validate_config(config: Any) -> None:
    if config.backend != "aws":
        raise ValueError("the EKS lifecycle requires shifter.yaml backend: aws")
    if config.deployment.profile not in {"dev", "proof", "prod"}:
        raise ValueError("the AWS EKS bundle supports only dev, proof, and prod profiles")


def _validated_images(images: Mapping[str, object]) -> dict[str, str]:
    if not images:
        raise ValueError("at least one attested image identity is required")
    validated: dict[str, str] = {}
    for name, identity in images.items():
        if not isinstance(name, str) or not isinstance(identity, str) or not _IMAGE_IDENTITY_RE.fullmatch(identity):
            raise ValueError(f"image {name!r} must be an exact repository@sha256:<64 lowercase hex> identity")
        validated[name] = identity
    return validated


def _cidr_output(outputs: Mapping[str, object], name: str) -> list[str]:
    values = _output(outputs, name)
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"EKS Terraform output {name!r} must be a list of CIDR strings")
    return values


def _runtime_environment(profile: str) -> str:
    return {"dev": "development", "prod": "production"}.get(profile, profile)


def _runtime_env(config: Any, outputs: Mapping[str, object]) -> dict[str, str]:
    raw = _output(outputs, "runtime_env")
    if not isinstance(raw, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) and value for key, value in raw.items()
    ):
        raise ValueError("runtime_env must map canonical runtime keys to non-empty string values")
    conflicting = sorted(_RENDERER_OWNED_RUNTIME_ENV.intersection(raw))
    if conflicting:
        raise ValueError("runtime_env must not override renderer-owned keys: " + ", ".join(conflicting))
    missing = sorted(AWS_EKS_REQUIRED_RUNTIME_ENV_KEYS.difference(raw))
    if missing:
        raise ValueError("runtime_env is missing required keys: " + ", ".join(missing))
    domain = config.deployment.domain
    return {
        **dict(raw),
        "AUTH_PROVIDER": "oidc",
        "CLOUD_PROVIDER": "aws",
        "DJANGO_ALLOWED_HOSTS": f"{domain},localhost,127.0.0.1",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{domain}",
        "ENVIRONMENT": _runtime_environment(config.deployment.profile),
        "SITE_URL": f"https://{domain}",
    }


def _required_file(path: str | Path | None, *, label: str) -> Path:
    if path is None or not str(path).strip():
        raise ValueError(f"{label} is required")
    resolved = Path(path)
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist")
    return resolved


def _validate_terraform_inputs(path: Path, config: Any) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("the protected EKS Terraform input file must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("the protected EKS Terraform input file must contain a JSON object")
    missing = sorted(_REQUIRED_TERRAFORM_INPUTS.difference(payload))
    if missing:
        raise ValueError("the protected EKS Terraform input file is missing: " + ", ".join(missing))
    if payload["aws_region"] != config.settings["region"]:
        raise ValueError("the protected EKS Terraform input region does not match shifter.yaml")
    if payload["domain_name"] != config.deployment.domain:
        raise ValueError("the protected EKS Terraform input domain does not match shifter.yaml")


def _bootstrap_cluster(outputs: Mapping[str, object]) -> None:
    roles = _output(outputs, "workload_role_arns")
    if not isinstance(roles, Mapping) or not isinstance(roles.get("ingress"), str):
        raise ValueError("workload_role_arns must include the ingress controller role")
    with tempfile.TemporaryDirectory(prefix="shifter-eks-bootstrap-") as staging:
        staging_path = Path(staging)
        for namespace, plane in _PLATFORM_NAMESPACES.items():
            manifest = {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": namespace,
                    "labels": {
                        "app.kubernetes.io/part-of": "shifter",
                        "shifter.dev/plane": plane,
                        "pod-security.kubernetes.io/audit": "restricted",
                        "pod-security.kubernetes.io/enforce": "restricted",
                        "pod-security.kubernetes.io/warn": "restricted",
                    },
                },
            }
            manifest_path = staging_path / f"{namespace}.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            run_cmd(["kubectl", "apply", "-f", str(manifest_path)])

    run_cmd(
        [
            "helm",
            "repo",
            "add",
            "eks",
            "https://aws.github.io/eks-charts",
            "--force-update",
        ]
    )
    run_cmd(["helm", "repo", "update", "eks"])
    run_cmd(
        [
            "helm",
            "upgrade",
            "--install",
            "aws-load-balancer-controller",
            "eks/aws-load-balancer-controller",
            "--namespace",
            "kube-system",
            "--version",
            _LOAD_BALANCER_CONTROLLER_CHART_VERSION,
            "--set-string",
            f"clusterName={_output(outputs, 'cluster_name')}",
            "--set",
            "serviceAccount.create=true",
            "--set-string",
            "serviceAccount.name=aws-load-balancer-controller",
            "--set-string",
            "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=" + str(roles["ingress"]),
            "--atomic",
            "--wait",
            "--timeout",
            "10m",
        ]
    )
    run_cmd(
        [
            "kubectl",
            "rollout",
            "status",
            "deployment/aws-load-balancer-controller",
            "--namespace",
            "kube-system",
            "--timeout=5m",
        ]
    )


def render_aws_values(
    config: Any,
    terraform_outputs: Mapping[str, object],
    images: Mapping[str, object],
) -> dict[str, object]:
    """Render non-secret AWS values from validated config, outputs, and digests."""
    _validate_config(config)
    roles = _output(terraform_outputs, "workload_role_arns")
    if not isinstance(roles, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in roles.items()):
        raise ValueError("workload_role_arns must map exact service-account names to IAM role ARNs")
    missing_roles = sorted(_WORKLOAD_ROLE_KEYS.difference(roles))
    if missing_roles:
        raise ValueError("workload_role_arns is missing chart workload roles: " + ", ".join(missing_roles))
    return {
        "provider": {"name": "aws"},
        "deployment": {"name": config.deployment.name, "profile": config.deployment.profile},
        "capabilities": {"kubernetesJobLauncher": False},
        "edge": {
            "hostname": config.deployment.domain,
            "certificateArn": _output(terraform_outputs, "certificate_arn"),
            "wafAclArn": _output(terraform_outputs, "waf_acl_arn"),
            "ingress": {
                "enabled": True,
                "className": "alb",
                "annotations": {
                    "alb.ingress.kubernetes.io/scheme": "internet-facing",
                    "alb.ingress.kubernetes.io/target-type": "ip",
                    "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS":443}]',
                    "alb.ingress.kubernetes.io/ssl-redirect": "443",
                    "alb.ingress.kubernetes.io/certificate-arn": _output(terraform_outputs, "certificate_arn"),
                    "alb.ingress.kubernetes.io/wafv2-acl-arn": _output(terraform_outputs, "waf_acl_arn"),
                },
                "host": config.deployment.domain,
                # TLS terminates at the AWS Load Balancer Controller using ACM,
                # so no Kubernetes TLS Secret is created or placed in values.
                "tls": {"enabled": False, "secretName": ""},
                "gcpManagedTls": {
                    "enabled": False,
                    "certificateName": "platform-managed-cert",
                    "frontendConfigName": "platform-frontend-config",
                },
            },
        },
        "network": {
            "enabled": True,
            "ingressSourceCidrs": _cidr_output(terraform_outputs, "ingress_source_cidrs"),
            "providerApiCidrs": _cidr_output(terraform_outputs, "provider_api_cidrs"),
            "privateServiceCidrs": _cidr_output(terraform_outputs, "private_service_cidrs"),
            "kubernetesApiCidrs": _cidr_output(terraform_outputs, "kubernetes_api_cidrs"),
            "rangeClusterApiCidrs": [],
            "rangeClusterApiPort": 6444,
            "rangeAccessCidrs": [],
            "rangeAccessPorts": [22, 3389],
        },
        "identity": {"serviceAccountRoleArns": {key: roles[key] for key in sorted(_WORKLOAD_ROLE_KEYS)}},
        "runtimeEnv": _runtime_env(config, terraform_outputs),
        "runtime": {
            # References only. entrypoint.sh hydrates values from Secrets Manager
            # in-process; raw values never enter Helm history, ConfigMaps, or argv.
            "secretReferences": {
                "app": config.secrets["django_secret_key"],
                "database": config.secrets["db_password"],
            }
        },
        "images": _validated_images(images),
    }


def _read_images(path: str | Path) -> dict[str, object]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not read the attested image identity file") from exc
    if not isinstance(raw, dict):
        raise ValueError("the attested image identity file must contain a JSON object")
    return raw


def _terraform_outputs(root: Path, *, aws_profile: str | None) -> dict[str, object]:
    result = run_cmd(
        ["terraform", f"-chdir={root}", "output", "-json"],
        capture=True,
        profile=aws_profile,
    )
    stdout = getattr(result, "stdout", "")
    try:
        outputs = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("EKS Terraform output was not valid JSON") from exc
    if not isinstance(outputs, dict):
        raise RuntimeError("EKS Terraform output must be a JSON object")
    return outputs


def deploy_eks(
    config_path: str | Path,
    images_path: str | Path,
    *,
    backend_config_path: str | Path | None = None,
    terraform_inputs_path: str | Path | None = None,
    aws_profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """Apply a saved EKS plan and atomically roll out the shared Helm chart."""
    config = load_root_config(config_path)
    _validate_config(config)
    profile = config.deployment.profile
    preflight_gate(Cloud.AWS, Mode.LOCAL, profile, headless=True)
    root = eks_root(profile)
    backend_config = _required_file(backend_config_path, label="EKS Terraform backend config")
    terraform_inputs = _required_file(terraform_inputs_path, label="EKS Terraform input file")
    _validate_terraform_inputs(terraform_inputs, config)
    plan_name = "shifter-eks.tfplan"
    run_cmd(
        [
            "terraform",
            f"-chdir={root}",
            "init",
            "-input=false",
            "-reconfigure",
            f"-backend-config={backend_config}",
        ],
        dry_run=dry_run,
        profile=aws_profile,
    )
    run_cmd(
        [
            "terraform",
            f"-chdir={root}",
            "plan",
            "-input=false",
            f"-var-file={terraform_inputs}",
            f"-out={plan_name}",
        ],
        dry_run=dry_run,
        profile=aws_profile,
    )
    run_cmd(["terraform", f"-chdir={root}", "apply", plan_name], dry_run=dry_run, profile=aws_profile)
    health_url = f"https://{config.deployment.domain}/health/"
    if dry_run:
        return {"backend": "aws", "profile": profile, "health_url": health_url}

    outputs = _terraform_outputs(root, aws_profile=aws_profile)
    run_cmd(
        [
            "aws",
            "eks",
            "update-kubeconfig",
            "--name",
            str(_output(outputs, "cluster_name")),
            "--region",
            str(config.settings["region"]),
            "--role-arn",
            str(_output(outputs, "cluster_access_role_arn")),
            "--alias",
            f"shifter-{profile}",
        ],
        profile=aws_profile,
    )
    _bootstrap_cluster(outputs)
    values = render_aws_values(config, outputs, _read_images(images_path))
    chart = get_repo_root() / "platform" / "charts" / "shifter"
    provider_values = chart / f"values-aws-{profile}.yaml"
    with tempfile.TemporaryDirectory(prefix="shifter-eks-values-") as staging:
        values_path = Path(staging) / "values.json"
        values_path.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        run_cmd(["helm", "lint", str(chart), "--values", str(provider_values), "--values", str(values_path)])
        run_cmd(
            [
                "helm",
                "template",
                _HELM_RELEASE,
                str(chart),
                "--values",
                str(provider_values),
                "--values",
                str(values_path),
            ]
        )
        run_cmd(
            [
                "helm",
                "upgrade",
                "--install",
                _HELM_RELEASE,
                str(chart),
                "--namespace",
                _EKS_NAMESPACE,
                "--create-namespace",
                "--values",
                str(provider_values),
                "--values",
                str(values_path),
                "--atomic",
                "--wait",
                "--timeout",
                "15m",
            ]
        )
    run_cmd(["curl", "--fail", "--silent", "--show-error", "--max-time", "30", health_url])
    return {"backend": "aws", "profile": profile, "health_url": health_url}


def teardown_eks(
    config_path: str | Path,
    *,
    backend_config_path: str | Path | None = None,
    terraform_inputs_path: str | Path | None = None,
    aws_profile: str | None = None,
    dry_run: bool = False,
) -> None:
    """Remove the Helm release and only the isolated EKS Terraform root."""
    config = load_root_config(config_path)
    _validate_config(config)
    root = eks_root(config.deployment.profile)
    backend_config = _required_file(backend_config_path, label="EKS Terraform backend config")
    terraform_inputs = _required_file(terraform_inputs_path, label="EKS Terraform input file")
    run_cmd(["helm", "uninstall", _HELM_RELEASE, "--namespace", _EKS_NAMESPACE, "--wait"], dry_run=dry_run)
    run_cmd(
        [
            "terraform",
            f"-chdir={root}",
            "init",
            "-input=false",
            "-reconfigure",
            f"-backend-config={backend_config}",
        ],
        dry_run=dry_run,
        profile=aws_profile,
    )
    run_cmd(
        [
            "terraform",
            f"-chdir={root}",
            "destroy",
            "-auto-approve",
            f"-var-file={terraform_inputs}",
        ],
        dry_run=dry_run,
        profile=aws_profile,
    )
    if dry_run:
        return
    state = run_cmd(
        ["terraform", f"-chdir={root}", "state", "list"],
        capture=True,
        profile=aws_profile,
    )
    if getattr(state, "stdout", "").strip():
        raise RuntimeError("EKS teardown postcondition failed: isolated EKS state is not empty")
