"""GKE-native GCP adapter over the provider-neutral Kubernetes task runner.

The generic Kubernetes Job mechanics (manifest build, create-or-observe
lifecycle, sensitive-env Secret projection, interrupt, status) live in
``shared.cloud.kubernetes`` (#1824). This module is the thin GCP adapter: it
builds a ``KubernetesTaskProfile`` from Django settings plus the GCP-specific
identity/label/hardening choices and composes ``KubernetesTaskRunner``.

It remains the public face of the adapter so callers keep using
``from shared.cloud.gcp.task_runner import GCPTaskRunner`` exactly as before, and
the AWS/GCP cloud-adapter seam (ADR-005-R1) still pairs
``cloud/aws/task_runner.py`` with ``cloud/gcp/task_runner.py``. The Workload
Identity KSA (``ENGINE_TASK_SERVICE_ACCOUNT_NAME``) and the
``shifter.dev/task-runner: gcp`` label are GCP wiring injected here, never generic
Kubernetes defaults.
"""

from __future__ import annotations

from django.conf import settings

from shared.cloud import PROVISIONER_CONTAINER_NAME
from shared.cloud.kubernetes import KubernetesTaskProfile, KubernetesTaskRunner, ProvisionerHardeningProfile

# GCP task-runner provider tag stamped on Job/Secret metadata.
_SHIFTER_TASK_RUNNER_GCP = "gcp"

# Exclusive provisioner node-pool placement (#1711). Provisioner Jobs SSH-drive
# range hosts and probe the OpenVPN gateway; pinning them to the tainted
# provisioner pool gives their pods alias IPs from the provisioner pod range,
# which is the only source the range VPC's management ingress admits. The node
# node label matches the pool's ``node-restriction.kubernetes.io/shifter-pool``
# label and the toleration matches its ``dedicated=provisioner:NoSchedule`` taint
# (both in platform/terraform/gcp/modules/portal/gke/main.tf). The selector keys
# on the NodeRestriction-protected prefix (not the generic ``role`` label) so a
# compromised kubelet cannot self-label its node to attract provisioner Jobs
# (#1711 codex security finding).
_PROVISIONER_NODE_SELECTOR = {"node-restriction.kubernetes.io/shifter-pool": "provisioner"}
_PROVISIONER_TOLERATIONS = (("dedicated", "Equal", "provisioner", "NoSchedule"),)

# Provisioner runtime identity (issue #950/#1103). Non-root uid/gid the
# provisioner image runs as; the hardened writable surface is chowned to the gid.
_PROVISIONER_RUN_AS_UID = 1000
_PROVISIONER_RUN_AS_GID = 1000

# Memory-backed workspace volume size cap. Terraform staging trees are tiny
# (a few MB), but a runaway plan log or provider download could otherwise
# consume node memory unbounded. 256Mi is generous for the staged terraform/
# tree plus typical plan output without putting the node under pressure.
_PROVISIONER_WORKSPACE_SIZE_LIMIT = "256Mi"

# Writable mount points the provisioner image needs at runtime. /app and the
# rest of the root filesystem are read-only (issue #1103); these explicit
# emptyDir volumes are the only paths the runtime user can write to.
# - workspace: terraform_base._stage_workspace target. Memory-backed (medium=Memory)
#   so terraform.tfvars.json (which can carry secrets) does not persist on disk;
#   capped at _PROVISIONER_WORKSPACE_SIZE_LIMIT to bound the worst-case node memory
#   pressure from a runaway plan log or large provider download.
# - /tmp: Python tempfile, kubectl temp kubeconfigs (gdc_*), etc.
# - tf plugin cache and pulumi home: Terraform/Pulumi tool state under HOME.
_PROVISIONER_WRITABLE_MOUNTS: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("provisioner-workspace", "/var/run/provisioner/workspace", "Memory", _PROVISIONER_WORKSPACE_SIZE_LIMIT),
    ("tmp", "/tmp", None, None),  # noqa: S108 # nosec B108 — Kubernetes mount path, not a tempfile API call
    ("tf-plugin-cache", "/home/appuser/.terraform.d/plugin-cache", None, None),
    ("pulumi-home", "/home/appuser/.pulumi", None, None),
)


def _build_gcp_task_profile() -> KubernetesTaskProfile:
    """Resolve the GCP Kubernetes task profile from Django settings at call time.

    Reading here (rather than at construction) preserves the historical timing:
    ``ENGINE_TASK_*`` values are owned by the runtime env/Helm renderers and read
    when a task is launched.
    """
    return KubernetesTaskProfile(
        runner_label_value=_SHIFTER_TASK_RUNNER_GCP,
        service_account_name=str(getattr(settings, "ENGINE_TASK_SERVICE_ACCOUNT_NAME", "") or ""),
        image_pull_policy=getattr(settings, "ENGINE_TASK_IMAGE_PULL_POLICY", "IfNotPresent"),
        backoff_limit=getattr(settings, "ENGINE_TASK_BACKOFF_LIMIT", 0),
        ttl_seconds_after_finished=getattr(settings, "ENGINE_TASK_TTL_SECONDS_AFTER_FINISHED", 3600),
        hardening=ProvisionerHardeningProfile(
            container_name=PROVISIONER_CONTAINER_NAME,
            run_as_uid=_PROVISIONER_RUN_AS_UID,
            run_as_gid=_PROVISIONER_RUN_AS_GID,
            writable_mounts=_PROVISIONER_WRITABLE_MOUNTS,
        ),
        node_selector=_PROVISIONER_NODE_SELECTOR,
        tolerations=_PROVISIONER_TOLERATIONS,
    )


class GCPTaskRunner(KubernetesTaskRunner):
    """GCP adapter: the neutral Kubernetes runner wired with the GCP task profile.

    For GCP the ECS-shaped TaskRunner interface is reinterpreted by the neutral
    core: ``cluster`` is the Kubernetes namespace, ``task_definition`` the image,
    and ``command`` the container args (image ENTRYPOINT preserved).
    """

    def __init__(self) -> None:
        super().__init__(_build_gcp_task_profile)


__all__ = ("PROVISIONER_CONTAINER_NAME", "GCPTaskRunner")
