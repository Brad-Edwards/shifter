"""Restricted, networkless OCI worker execution for tenant-owned runtime plugins.

The namespace and its policies are shipped with the tenant deployment. Plugin
manifests cannot change the worker identity, environment, mounts, or resources.
"""

from __future__ import annotations

from typing import Any

from shifter_adapter_sdk.runtime import InspectionInput, InspectionResult, RuntimeInput, RuntimePlan, parse_result

from shared.cloud.kubernetes import KubernetesTaskProfile, KubernetesTaskRunner, ProvisionerHardeningProfile
from shared.cloud.kubernetes.naming import build_idempotent_job_name

PLUGIN_NAMESPACE = "shifter-plugins"
PLUGIN_CONTAINER = "runtime-plugin"
PLUGIN_SERVICE_ACCOUNT = "plugin-worker"


def plugin_task_profile(*, image_pull_secret: str = "") -> KubernetesTaskProfile:
    """The same restricted posture applies to inspection and range invocations."""
    return KubernetesTaskProfile(
        runner_label_value="runtime-plugin",
        service_account_name=PLUGIN_SERVICE_ACCOUNT,
        image_pull_policy="Always",
        backoff_limit=0,
        ttl_seconds_after_finished=600,
        hardening=ProvisionerHardeningProfile(
            container_name=PLUGIN_CONTAINER,
            run_as_uid=65532,
            run_as_gid=65532,
            writable_mounts=(("tmp", "/tmp", "Memory", "16Mi"),),  # noqa: S108 # nosec B108
        ),
        image_pull_secrets=(image_pull_secret,) if image_pull_secret else (),
        resource_requests={"cpu": "100m", "memory": "64Mi", "ephemeral-storage": "16Mi"},
        resource_limits={"cpu": "1", "memory": "256Mi", "ephemeral-storage": "64Mi"},
        active_deadline_seconds=120,
        runtime_class_name="gvisor",
        node_selector={"node-restriction.kubernetes.io/shifter-pool": "runtime-plugin"},
        tolerations=(("shifter.dev/runtime-plugin", "Equal", "true", "NoSchedule"),),
    )


def plugin_task_identity(request: RuntimeInput | InspectionInput) -> dict[str, Any]:
    """Identity derives exclusively from the host's pinned invocation."""
    return {
        "task_identity": str(request.invocation_id),
        "service_account_name": PLUGIN_SERVICE_ACCOUNT,
        "container_name": PLUGIN_CONTAINER,
        "image": request.manifest.worker_image,
        "command": [],
    }


def plugin_task_ref(request: RuntimeInput | InspectionInput) -> str:
    return f"{PLUGIN_NAMESPACE}/{build_idempotent_job_name(PLUGIN_CONTAINER, str(request.invocation_id))}"


def launch_plugin(
    request: RuntimeInput | InspectionInput,
    *,
    image_pull_secret: str = "",
) -> None:
    """Idempotently dispatch a fixed worker profile without application credentials."""
    runner = KubernetesTaskRunner(plugin_task_profile(image_pull_secret=image_pull_secret))
    runner.run_task(
        task_definition=request.manifest.worker_image,
        cluster=PLUGIN_NAMESPACE,
        command=[],
        container_name=PLUGIN_CONTAINER,
        env_overrides={"SHIFTER_PLUGIN_INPUT": request.model_dump_json()},
        task_identity=str(request.invocation_id),
    )


def observe_plugin(request: RuntimeInput | InspectionInput) -> RuntimePlan | InspectionResult | None:
    """A malformed or replayed response never becomes an accepted result."""
    runner = KubernetesTaskRunner(plugin_task_profile())
    raw = runner.get_task_output(PLUGIN_NAMESPACE, plugin_task_ref(request), plugin_task_identity(request))
    return None if raw is None else parse_result(raw, request)


def interrupt_plugin(request: RuntimeInput | InspectionInput) -> str:
    """Only stop the reserved invocation; existing range cleanup is independent."""
    return KubernetesTaskRunner(plugin_task_profile()).interrupt_task(
        PLUGIN_NAMESPACE,
        plugin_task_ref(request),
        plugin_task_identity(request),
    )
