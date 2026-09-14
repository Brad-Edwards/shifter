"""Read back the installed Kubernetes half of a preparation cloud grant."""

from shared.cloud.preparation_installation import PreparationInstallation, render_preparation_installation


class KubernetesInstallationReader:
    """Use the operator's Kubernetes client without exposing any Secret payloads."""

    def __init__(self):
        from shared.cloud.kubernetes._client import load_kubernetes_api

        _, core, client, _ = load_kubernetes_api()
        self.client = client
        self.core = core
        self.api = client.ApiClient()

    def __call__(self, resource):
        methods = {
            "Namespace": (self.core, "read_namespace"),
            "ServiceAccount": (self.core, "read_namespaced_service_account"),
            "ConfigMap": (self.core, "read_namespaced_config_map"),
            "ResourceQuota": (self.core, "read_namespaced_resource_quota"),
            "Role": (self.client.RbacAuthorizationV1Api(), "read_namespaced_role"),
            "RoleBinding": (self.client.RbacAuthorizationV1Api(), "read_namespaced_role_binding"),
            "Deployment": (self.client.AppsV1Api(), "read_namespaced_deployment"),
            "NetworkPolicy": (self.client.NetworkingV1Api(), "read_namespaced_network_policy"),
            "ValidatingAdmissionPolicy": (self.client.AdmissionregistrationV1Api(), "read_validating_admission_policy"),
            "ValidatingAdmissionPolicyBinding": (
                self.client.AdmissionregistrationV1Api(),
                "read_validating_admission_policy_binding",
            ),
        }
        owner, name = methods[resource["kind"]]
        arguments = {key: value for key, value in resource["metadata"].items() if key in {"name", "namespace"}}
        try:
            response = getattr(owner, name)(**arguments, _request_timeout=30)
        except Exception as exc:
            raise ValueError("preparation Kubernetes installation readback failed") from exc
        return self.api.sanitize_for_serialization(response)


def verify_kubernetes_installation(configuration: PreparationInstallation, read=None) -> str:
    """Check deployed identities, admission, RBAC, budgets, isolation and controller rollout.

    This attests only Kubernetes resources. The installer must independently
    verify the matching cloud IAM/network before activating application authority.
    """
    reader = read or KubernetesInstallationReader()
    for expected in render_preparation_installation(configuration):
        observed = reader(expected)
        if not _contains(observed, expected):
            raise ValueError("preparation Kubernetes installation does not match its configuration")
        status = observed.get("status", {})
        if expected["kind"] in {"Deployment", "ValidatingAdmissionPolicy"} and status.get(
            "observedGeneration"
        ) != observed["metadata"].get("generation"):
            raise ValueError("preparation installation has not converged")
        if expected["kind"] == "ValidatingAdmissionPolicy":
            if "typeChecking" not in status or status["typeChecking"].get("expressionWarnings"):
                raise ValueError("preparation admission policy has not passed type checking")
        elif expected["kind"] == "Deployment" and (
            status.get("readyReplicas") != 1 or status.get("updatedReplicas") != 1
        ):
            raise ValueError("preparation controller is not ready")
    return configuration.digest


_EXECUTION_FIELDS = frozenset(
    {
        "command",
        "args",
        "initContainers",
        "ephemeralContainers",
        "hostNetwork",
        "hostPID",
        "hostIPC",
        "shareProcessNamespace",
        "hostUsers",
        "hostPath",
        "projected",
        "secret",
        "add",
        "privileged",
        "seLinuxOptions",
        "sysctls",
        "procMount",
        "dnsConfig",
        "hostAliases",
        "env",
        "envFrom",
        "valueFrom",
        "serviceAccountName",
        "automountServiceAccountToken",
        "matchExpressions",
    }
)


def _contains(observed, expected, field=""):
    """Allow API defaults and status fields, but no additional list authorities."""
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return False
        # EnvVar.value defaults to the empty string and is omitted by the API.
        # A valueFrom reference would change the binding and is never a default.
        if field == "env" and expected.get("value") == "" and "value" not in observed:
            observed = dict(observed, value="")
        # NetworkPolicy empty rule lists are also omitted. The explicit
        # policyTypes remains mandatory, so missing ingress still denies it.
        if field == "spec" and "policyTypes" in expected:
            observed = dict(observed)
            for key in ("ingress", "egress"):
                if expected.get(key) == [] and key not in observed:
                    observed[key] = []
        if (not expected or field in {"labels", "matchLabels"}) and set(observed) != set(expected):
            return False
        if (set(observed) - set(expected)) & _EXECUTION_FIELDS:
            return False
        return all(key in observed and _contains(observed[key], value, key) for key, value in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(observed) == len(expected)
            and all(_contains(actual, wanted, field) for actual, wanted in zip(observed, expected, strict=True))
        )
    return observed == expected
