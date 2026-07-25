"""Contract tests for the backend-neutral Shifter Helm chart."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


CHART_DIR = Path(__file__).resolve().parents[1]
VALUES_FILES = {
    path.stem.removeprefix("values-"): path
    for path in CHART_DIR.glob("values-*.yaml")
}


def _helm(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", *args],
        check=check,
        cwd=CHART_DIR.parents[2],
        capture_output=True,
        text=True,
    )


def _render(*values_files: Path) -> tuple[str, list[dict[str, object]]]:
    args = ["template", "contract-test", str(CHART_DIR)]
    for values_file in values_files:
        args.extend(["-f", str(values_file)])
    rendered = _helm(*args).stdout
    documents = [
        document
        for document in yaml.safe_load_all(rendered)
        if isinstance(document, dict)
    ]
    return rendered, documents


def _identity(document: dict[str, object]) -> tuple[str, str]:
    metadata = document.get("metadata", {})
    assert isinstance(metadata, dict)
    return str(document.get("kind")), str(metadata.get("name"))


class BackendNeutralChartContractTests(unittest.TestCase):
    def test_chart_has_schema_and_all_backend_profiles(self) -> None:
        schema = json.loads((CHART_DIR / "values.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(
            set(VALUES_FILES),
            {"aws-dev", "aws-proof", "aws-prod", "gcp-dev", "gcp-prod"},
        )

    def test_neutral_defaults_have_no_provider_specific_artifacts(self) -> None:
        rendered, _ = _render()
        forbidden = (
            "cloud.google.com/",
            "networking.gke.io/",
            "gce",
            "35.191.0.0/16",
            "130.211.0.0/22",
            "199.36.153.",
            "us-docker.pkg.dev",
            "amazonaws.com",
        )
        for marker in forbidden:
            self.assertNotIn(marker, rendered)

    def test_every_profile_renders_digest_pinned_images(self) -> None:
        for profile, values_file in VALUES_FILES.items():
            with self.subTest(profile=profile):
                _, documents = _render(values_file)
                for document in documents:
                    if document.get("kind") != "Deployment":
                        continue
                    pod_template = document["spec"]["template"]
                    containers = pod_template["spec"]["containers"]
                    for container in containers:
                        image = container["image"]
                        self.assertRegex(
                            image,
                            r"^[^@\s]+@sha256:[0-9a-f]{64}$",
                            f"{profile}: {image}",
                        )

    def test_aws_profiles_exclude_kubernetes_job_launcher_capability(self) -> None:
        forbidden = {
            ("Deployment", "worker-provisioner-launcher"),
            ("ServiceAccount", "provisioner-launcher"),
            ("ServiceAccount", "provisioner"),
            ("Role", "job-launcher"),
            ("RoleBinding", "job-launcher-provisioner-launcher"),
            ("ValidatingAdmissionPolicy", "restrict-provisioner-jobs"),
            ("ValidatingAdmissionPolicyBinding", "restrict-provisioner-jobs"),
        }
        for profile in ("aws-dev", "aws-proof", "aws-prod"):
            with self.subTest(profile=profile):
                _, documents = _render(VALUES_FILES[profile])
                identities = {_identity(document) for document in documents}
                self.assertTrue(forbidden.isdisjoint(identities))

    def test_gcp_profiles_retain_compatible_provider_capabilities(self) -> None:
        for profile in ("gcp-dev", "gcp-prod"):
            with self.subTest(profile=profile):
                rendered, documents = _render(VALUES_FILES[profile])
                identities = {_identity(document) for document in documents}
                self.assertIn(("BackendConfig", "portal-web"), identities)
                self.assertIn(("Deployment", "worker-provisioner-launcher"), identities)
                self.assertIn(("Role", "job-launcher"), identities)
                self.assertIn("cloud.google.com/neg", rendered)

    def test_aws_generated_projection_wires_edge_identity_and_secret_references(self) -> None:
        digest = "a" * 64
        generated = {
            "provider": {"name": "aws"},
            "deployment": {"name": "shifter", "profile": "dev"},
            "edge": {
                "hostname": "shifter.example.com",
                "certificateArn": "arn:aws:acm:us-east-2:123456789012:certificate/example",
                "wafAclArn": "arn:aws:wafv2:us-east-2:123456789012:regional/webacl/example/id",
                "ingress": {
                    "enabled": True,
                    "className": "alb",
                    "annotations": {
                        "alb.ingress.kubernetes.io/certificate-arn": (
                            "arn:aws:acm:us-east-2:123456789012:certificate/example"
                        ),
                        "alb.ingress.kubernetes.io/wafv2-acl-arn": (
                            "arn:aws:wafv2:us-east-2:123456789012:regional/webacl/example/id"
                        ),
                    },
                    "host": "shifter.example.com",
                    "tls": {"enabled": False, "secretName": ""},
                    "gcpManagedTls": {
                        "enabled": False,
                        "certificateName": "platform-managed-cert",
                        "frontendConfigName": "platform-frontend-config",
                    },
                },
            },
            "identity": {
                "serviceAccountRoleArns": {
                    "portal": "arn:aws:iam::123456789012:role/shifter-dev-portal",
                    "workers": "arn:aws:iam::123456789012:role/shifter-dev-workers",
                    "ctfScheduler": "arn:aws:iam::123456789012:role/shifter-dev-ctf-scheduler",
                }
            },
            "runtimeEnv": {
                "AUTH_PROVIDER": "oidc",
                "CLOUD_PROVIDER": "aws",
                "ENGINE_TASK_CLUSTER": "arn:aws:ecs:us-east-2:123456789012:cluster/shifter-dev",
                "ENVIRONMENT": "development",
                "OIDC_SECRET_ID": "shifter/dev/cognito",
                "QUEUE_ENGINE_CONSUMER_ID": "https://sqs.us-east-2.amazonaws.com/123456789012/engine",
                "STORAGE_BUCKET_NAME": "shifter-dev-storage",
            },
            "runtime": {
                "secretReferences": {
                    "app": "shifter/dev/app",
                    "database": "shifter/dev/database",
                }
            },
            "images": {
                "platform": f"example.invalid/shifter/platform@sha256:{digest}",
                "guacd": f"example.invalid/shifter/guacd@sha256:{digest}",
                "guacamoleClient": f"example.invalid/shifter/guacamole-client@sha256:{digest}",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            generated_path = Path(directory) / "generated.json"
            generated_path.write_text(json.dumps(generated), encoding="utf-8")
            rendered, documents = _render(VALUES_FILES["aws-dev"], generated_path)

        self.assertIn('ingressClassName: "alb"', rendered)
        self.assertIn('host: "shifter.example.com"', rendered)
        self.assertIn("eks.amazonaws.com/role-arn", rendered)
        self.assertIn('CLOUD_PROVIDER: "aws"', rendered)
        self.assertIn('ENVIRONMENT: "development"', rendered)
        self.assertIn('OIDC_SECRET_ID: "shifter/dev/cognito"', rendered)
        self.assertIn('APP_SECRET_ID: "shifter/dev/app"', rendered)
        self.assertIn('DB_SECRET_ID: "shifter/dev/database"', rendered)
        self.assertNotIn("kind: Secret", rendered)
        self.assertNotIn(("Deployment", "worker-provisioner-launcher"), {_identity(d) for d in documents})

    def test_security_and_default_deny_are_preserved_for_every_profile(self) -> None:
        for profile, values_file in VALUES_FILES.items():
            with self.subTest(profile=profile):
                _, documents = _render(values_file)
                identities = {_identity(document) for document in documents}
                self.assertIn(("NetworkPolicy", "default-deny-platform"), identities)
                self.assertIn(("NetworkPolicy", "default-deny-jobs"), identities)
                for document in documents:
                    if document.get("kind") != "Deployment":
                        continue
                    pod_spec = document["spec"]["template"]["spec"]
                    self.assertEqual(
                        pod_spec["securityContext"]["seccompProfile"]["type"],
                        "RuntimeDefault",
                    )
                    for container in pod_spec["containers"]:
                        context = container["securityContext"]
                        self.assertFalse(context["allowPrivilegeEscalation"])
                        self.assertTrue(context["readOnlyRootFilesystem"])
                        self.assertTrue(context["runAsNonRoot"])
                        self.assertIn("ALL", context["capabilities"]["drop"])
                        self.assertIn("requests", container["resources"])
                        self.assertIn("limits", container["resources"])

    def test_schema_rejects_tag_shaped_image_identity(self) -> None:
        result = _helm(
            "template",
            "contract-test",
            str(CHART_DIR),
            "--set-string",
            "images.platform=latest",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("images.platform", result.stderr)

    def test_schema_rejects_provider_feature_without_capability(self) -> None:
        result = _helm(
            "template",
            "contract-test",
            str(CHART_DIR),
            "--set",
            "services.portal.backendConfig.enabled=true",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gcpBackendConfig", result.stderr)


if __name__ == "__main__":
    unittest.main()
