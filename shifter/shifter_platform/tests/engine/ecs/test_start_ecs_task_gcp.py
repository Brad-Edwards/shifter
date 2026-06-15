"""Behavior tests for the GCP task-runner configuration in engine.ecs.

GCP uses Kubernetes namespace/image settings and omits the AWS network config.
These drive the real configuration units that ``_start_ecs_task`` consumes for
GCP (``_get_engine_task_config`` and ``_get_gcp_provisioner_env_overrides``)
rather than patching ``get_task_runner``; that captures the same contract (no
network config, the GKE provisioner env, the #762 password exclusions) against
the real logic.
"""

import os
from unittest.mock import patch

GCP_ENV = {
    "ENVIRONMENT": "gcp-dev",
    "CLOUD_PROVIDER": "gcp",
    "CLOUD_REGION": "us-central1",
    "GCP_REGION": "us-central1",
    "GCP_PROJECT_ID": "shifter-gcp-dev",
    "GOOGLE_CLOUD_PROJECT": "shifter-gcp-dev",
    "DB_HOST": "10.0.0.10",
    "DB_PORT": "5432",
    "DB_NAME": "shifter",
    "DB_USER": "shifter",
    "DB_PASSWORD": "secret",
    "RANGE_EVENTS_TOPIC_ID": "projects/shifter-gcp-dev/topics/shifter-gcp-dev-events",
    "RANGE_NETWORK_ID": "projects/shifter-gcp-dev/global/networks/shifter-gcp-dev-range",
    "RANGE_NETWORK_CIDR": "10.50.0.0/16",
    "PORTAL_NETWORK_CIDRS": "10.40.0.0/20,10.44.0.0/16",
    "GDC_ACCESS_SECRET_ID": "projects/shifter-gcp-dev/secrets/shifter-gcp-dev-gdc-access",
    "GDC_VM_IMAGE_GCS_SECRET_ID": "projects/shifter-gcp-dev/secrets/shifter-gcp-dev-gdc-vm-image-gcs",
    "GDC_KALI_IMAGE_URL": "gs://images/kali.qcow2",
    # Shared guest passwords MUST NOT flow into the provisioner env after #762;
    # they are set here to prove they are filtered out, not forwarded.
    "GDC_WINDOWS_ADMIN_PASSWORD": "WinPass!",
    "GDC_KALI_PASSWORD": "KaliPass!",
    "GDC_UBUNTU_PASSWORD": "UbuntuPass!",
    # DC_DOMAIN_PASSWORD remains deployment-scoped and is forwarded.
    "DC_DOMAIN_PASSWORD": "DomainAdminPass!",
}


class TestGcpTaskConfig:
    def test_omits_network_config_for_gcp(self, settings):
        from engine.ecs import _get_engine_task_config

        settings.CLOUD_PROVIDER = "gcp"
        settings.ENGINE_TASK_CLUSTER = "shifter-jobs"
        settings.ENGINE_TASK_DEFINITION = (
            "us-central1-docker.pkg.dev/shifter-gcp-dev/shifter-gcp-dev-pulumi-provisioner:latest"
        )

        cluster, task_definition, network_config = _get_engine_task_config()
        assert cluster == "shifter-jobs"
        assert task_definition.endswith("pulumi-provisioner:latest")
        assert network_config is None


class TestGcpProvisionerEnvOverrides:
    def test_returns_none_for_non_gcp(self, settings):
        from engine.ecs import _get_gcp_provisioner_env_overrides

        settings.CLOUD_PROVIDER = "aws"
        assert _get_gcp_provisioner_env_overrides() is None

    def test_forwards_gke_runtime_contract(self, settings):
        from engine.ecs import _get_gcp_provisioner_env_overrides

        settings.CLOUD_PROVIDER = "gcp"
        with patch.dict(os.environ, GCP_ENV, clear=False):
            overrides = _get_gcp_provisioner_env_overrides()

        assert overrides["RANGE_NETWORK_ID"] == GCP_ENV["RANGE_NETWORK_ID"]
        assert overrides["RANGE_NETWORK_CIDR"] == GCP_ENV["RANGE_NETWORK_CIDR"]
        assert overrides["PORTAL_NETWORK_CIDRS"] == GCP_ENV["PORTAL_NETWORK_CIDRS"]
        assert overrides["GDC_ACCESS_SECRET_ID"] == GCP_ENV["GDC_ACCESS_SECRET_ID"]
        assert overrides["GDC_VM_IMAGE_GCS_SECRET_ID"] == GCP_ENV["GDC_VM_IMAGE_GCS_SECRET_ID"]
        assert overrides["GDC_KALI_IMAGE_URL"] == GCP_ENV["GDC_KALI_IMAGE_URL"]
        assert overrides["DB_HOST"] == GCP_ENV["DB_HOST"]

    def test_excludes_shared_guest_passwords(self, settings):
        from engine.ecs import _get_gcp_provisioner_env_overrides

        settings.CLOUD_PROVIDER = "gcp"
        with patch.dict(os.environ, GCP_ENV, clear=False):
            overrides = _get_gcp_provisioner_env_overrides()

        # #762: shared guest passwords are per-instance secrets, never forwarded.
        assert "GDC_WINDOWS_ADMIN_PASSWORD" not in overrides
        assert "GDC_KALI_PASSWORD" not in overrides
        assert "GDC_UBUNTU_PASSWORD" not in overrides
        # The deployment-scoped DC domain password is still forwarded.
        assert overrides["DC_DOMAIN_PASSWORD"] == GCP_ENV["DC_DOMAIN_PASSWORD"]
