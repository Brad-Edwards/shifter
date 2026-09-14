"""Operator-rendered preparation add-on for an existing GKE tenant.

Rendering grants no authority. Installation verification must read back these
resources and the separately provisioned cloud IAM before activating a grant.
Private adapter/image changes are runtime installations, not platform releases.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifact_preparation import ImageRef
from shared.operation_envelope import canonical_payload_digest
from shared.preparation_grant import DNSLabel, ImageDigest, PreparationGrantConfiguration

CONTROLLER = "preparation-controller"
SECRET_ENV = (
    "DB_SECRET_ID",
    "APP_SECRET_ID",
    "REDIS_SECRET_ID",
    "OIDC_SECRET_ID",
    "GUACAMOLE_SECRET_ID",
    "DC_DOMAIN_PASSWORD_SECRET_ID",
    "EMAIL_API_KEY_SECRET_ID",
)


class PreparationInstallation(BaseModel):
    """Operator inputs contain resource references, never cloud keys or registry passwords."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    grant: PreparationGrantConfiguration
    platform_namespace: DNSLabel
    controller_image: ImageDigest
    service_accounts: dict[Literal["builder", "verifier", "cleanup", "controller"], str]
    network_cidr: str
    cluster_name: DNSLabel
    cluster_location: DNSLabel
    input_images: list[ImageRef] = Field(default_factory=list, max_length=8)
    controller_secret_ids: dict[str, Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,255}$")]]

    @model_validator(mode="after")
    def validate_identities(self):
        import re
        from ipaddress import IPv4Network

        network = IPv4Network(self.network_cidr, strict=True)
        if not network.is_private or not 20 <= network.prefixlen <= 28:
            raise ValueError("preparation requires a bounded private IPv4 subnet")
        if not {"DB_SECRET_ID", "APP_SECRET_ID", "REDIS_SECRET_ID"}.issubset(self.controller_secret_ids) or not set(
            self.controller_secret_ids
        ).issubset(SECRET_ENV):
            raise ValueError("preparation controller requires explicit named runtime secret grants")

        if set(self.service_accounts) != {"builder", "verifier", "cleanup", "controller"}:
            raise ValueError("all preparation cloud identities are required")
        if len(set(self.service_accounts.values())) != 4 or self.platform_namespace == self.grant.namespace:
            raise ValueError("preparation controller and workers require separate identities and namespaces")
        for email in self.service_accounts.values():
            if not re.fullmatch(
                r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + re.escape(self.grant.project_id) + r"\.iam\.gserviceaccount\.com",
                email,
            ):
                raise ValueError("preparation cloud identity belongs to another project")
        return self

    @property
    def digest(self):
        return canonical_payload_digest(self.model_dump(mode="json"))

    @property
    def controller_name(self):
        """A new installation cannot retarget an older controller or its cleanup."""
        return "preparation-controller-" + self.grant.scope_digest[-12:]


def _resource(kind, name, namespace=None, *, api="v1", **fields):
    metadata = {"name": name, "labels": {"app.kubernetes.io/part-of": "shifter"}}
    if namespace:
        metadata["namespace"] = namespace
    return {"apiVersion": api, "kind": kind, "metadata": metadata, **fields}


def render_preparation_installation(configuration: PreparationInstallation) -> list[dict]:
    """Render a closed post-setup add-on; no platform or provisioner authority is widened."""
    from shared.cloud.preparation_policy import preparation_policy

    grant = configuration.grant
    controller = configuration.controller_name
    namespace = _resource("Namespace", grant.namespace)
    namespace["metadata"]["labels"].update(
        {
            "kubernetes.io/metadata.name": grant.namespace,
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/enforce-version": "v1.31",
        }
    )
    resources = [namespace]
    for role in ("builder", "verifier", "cleanup", "controller"):
        name = controller if role == "controller" else getattr(grant, role + "_service_account")
        scope = configuration.platform_namespace if role == "controller" else grant.namespace
        account = _resource("ServiceAccount", name, scope, automountServiceAccountToken=False)
        account["metadata"]["annotations"] = {"iam.gke.io/gcp-service-account": configuration.service_accounts[role]}
        resources.append(account)
    resources.extend(
        [
            _resource(
                "ConfigMap",
                "preparation-installation",
                grant.namespace,
                data={
                    "installation.json": configuration.model_dump_json(),
                    "installation_digest": configuration.digest,
                    "grant_digest": grant.digest,
                },
            ),
            _resource(
                "Role",
                CONTROLLER,
                grant.namespace,
                api="rbac.authorization.k8s.io/v1",
                rules=[
                    {
                        "apiGroups": ["batch"],
                        "resources": ["jobs"],
                        "verbs": ["create", "get", "list", "watch", "delete"],
                    },
                    {"apiGroups": ["batch"], "resources": ["jobs/status"], "verbs": ["get"]},
                    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch", "delete"]},
                    {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create", "get", "patch", "delete"]},
                ],
            ),
            _resource(
                "RoleBinding",
                CONTROLLER,
                grant.namespace,
                api="rbac.authorization.k8s.io/v1",
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": CONTROLLER},
                subjects=[
                    {"kind": "ServiceAccount", "name": controller, "namespace": configuration.platform_namespace}
                ],
            ),
            _resource(
                "ResourceQuota",
                "preparation-budget",
                grant.namespace,
                spec={
                    "hard": {
                        "pods": str(2 * grant.max_concurrent_operations),
                        "count/jobs.batch": str(2 * grant.max_concurrent_operations),
                        "requests.cpu": str(2 * grant.max_concurrent_operations),
                        "requests.memory": f"{grant.max_concurrent_operations}Gi",
                        "limits.cpu": str(2 * grant.max_concurrent_operations),
                        "limits.memory": f"{grant.max_concurrent_operations}Gi",
                    }
                },
            ),
            preparation_policy(configuration),
            _resource(
                "ValidatingAdmissionPolicyBinding",
                grant.namespace,
                api="admissionregistration.k8s.io/v1",
                spec={
                    "policyName": grant.namespace,
                    "validationActions": ["Deny"],
                    "matchResources": {
                        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": grant.namespace}}
                    },
                },
            ),
            _controller(configuration),
        ]
    )
    resources.extend(_network_policies(configuration))
    return resources


def _controller(configuration):
    labels = {
        "app.kubernetes.io/part-of": "shifter",
        "app.kubernetes.io/component": CONTROLLER,
        "shifter.io/preparation-scope": configuration.grant.scope_digest[-12:],
    }
    security = {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    return _resource(
        "Deployment",
        configuration.controller_name,
        configuration.platform_namespace,
        api="apps/v1",
        spec={
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": configuration.controller_name,
                    "nodeSelector": {"iam.gke.io/gke-metadata-server-enabled": "true"},
                    "automountServiceAccountToken": True,
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}, "fsGroup": 1000},
                    "containers": [
                        {
                            "name": CONTROLLER,
                            "image": configuration.controller_image,
                            "args": [
                                "python",
                                "manage.py",
                                "reconcile_artifact_preparation",
                                "--loop",
                                "--scope-digest",
                                configuration.grant.scope_digest,
                            ],
                            "envFrom": [{"configMapRef": {"name": "platform-runtime"}}],
                            "env": [
                                {"name": "SKIP_MIGRATIONS", "value": "1"},
                                *[
                                    {
                                        "name": key,
                                        "value": (
                                            f"projects/{configuration.grant.project_id}/secrets/{configuration.controller_secret_ids[key]}"
                                            if key in configuration.controller_secret_ids
                                            else ""
                                        ),
                                    }
                                    for key in SECRET_ENV
                                ],
                            ],
                            "securityContext": security,
                            "volumeMounts": [
                                {"name": "tmp", "mountPath": "/tmp"}  # noqa: S108  # nosec B108
                            ],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "512Mi"},
                                "limits": {"cpu": "1", "memory": "1Gi"},
                            },
                            "livenessProbe": {
                                "exec": {
                                    "command": [
                                        "python",
                                        "-c",
                                        "import os,time; "
                                        "assert time.time()-os.stat('/tmp/worker-preparation-heartbeat').st_mtime<900",
                                    ]
                                },
                                "initialDelaySeconds": 120,
                                "periodSeconds": 30,
                            },
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}}],
                },
            },
        },
    )


def _network_policies(configuration):
    dns = {
        "to": [
            {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": protocol, "port": 53} for protocol in ("TCP", "UDP")],
    }
    metadata = [
        {"to": [{"ipBlock": {"cidr": cidr}}], "ports": [{"protocol": "TCP", "port": port}]}
        for cidr, port in (("169.254.169.254/32", 80), ("169.254.169.252/32", 988))
    ]
    worker = _resource(
        "NetworkPolicy",
        "preparation-workers",
        configuration.grant.namespace,
        api="networking.k8s.io/v1",
        spec={
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [
                dns,
                *metadata,
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"],
                            }
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 443}],
                },
            ],
        },
    )
    controller = _resource(
        "NetworkPolicy",
        configuration.controller_name,
        configuration.platform_namespace,
        api="networking.k8s.io/v1",
        spec={
            "podSelector": {"matchLabels": {"shifter.io/preparation-scope": configuration.grant.scope_digest[-12:]}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [
                dns,
                *metadata,
                {
                    "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
                    "ports": [{"protocol": "TCP", "port": port} for port in (443, 5432, 6379)],
                },
            ],
        },
    )
    return [worker, controller]
