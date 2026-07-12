"""GKE-native Kubernetes Job adapter implementing TaskRunner protocol."""

from __future__ import annotations

import importlib
import logging
import os
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, cast

from django.conf import settings

from shared.cloud import PROVISIONER_CONTAINER_NAME
from shared.cloud.exceptions import CloudTaskError
from shared.cloud.gcp.base import build_idempotent_job_name, build_job_generate_name, parse_job_task_id
from shared.cloud.sensitive_env import split_env
from shared.log_sanitize import safe_log_fingerprint

__all__ = ("PROVISIONER_CONTAINER_NAME", "GCPTaskRunner")

logger = logging.getLogger(__name__)

_PROVISIONER_RUN_AS_UID = 1000
_PROVISIONER_RUN_AS_GID = 1000

# Canonical Kubernetes recommended labels referenced by multiple
# spec builders (Job metadata, Pod template, Secret metadata).
_K8S_LABEL_PART_OF = "app.kubernetes.io/part-of"
_K8S_LABEL_COMPONENT = "app.kubernetes.io/component"
_SHIFTER_PART_OF_VALUE = "shifter"
_SHIFTER_LABEL_TASK_RUNNER = "shifter.dev/task-runner"
_SHIFTER_TASK_RUNNER_GCP = "gcp"
_SHIFTER_ANNOTATION_TASK_IDENTITY = "shifter.dev/task-identity"
_KUBERNETES_REQUEST_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class _KubernetesApis:
    """Dynamically loaded Kubernetes API objects used during one launch."""

    batch: object
    core: object
    client: object
    exception: type[Exception]


class _OwnerReference(Protocol):
    """Attributes serialized from the dynamically loaded Kubernetes model."""

    api_version: object
    kind: object
    name: object
    uid: object
    controller: object
    block_owner_deletion: object


@dataclass(frozen=True)
class _JobIdentity:
    """Expected immutable identity of a deterministic provisioner Job."""

    job_name: str
    task_identity: str
    image: str
    command: list[str]
    container_name: str
    service_account_name: str
    secret_name: str | None


@dataclass(frozen=True)
class _JobLaunch:
    """Inputs shared by create and ambiguous-create recovery."""

    apis: _KubernetesApis
    namespace: str
    job: object
    identity: _JobIdentity | None
    secret_name: str | None


@dataclass(frozen=True)
class _RunTaskContext:
    """Resolved Kubernetes inputs for one TaskRunner invocation."""

    apis: _KubernetesApis
    namespace: str
    image: str
    command: list[str]
    container_name: str
    env_overrides: dict[str, str] | None
    task_identity: str | None
    identity: _JobIdentity | None
    sensitive_env: dict[str, str]
    secret_name: str | None


def _api_call(api: object, method: str, **kwargs: object) -> object:
    """Invoke one method on a dynamically loaded Kubernetes client object."""
    callback = getattr(api, method)
    return callback(**kwargs)


def _shifter_resource_labels(container_name: str, *, include_task_runner: bool) -> dict[str, str]:
    """Build the standard Shifter label set for K8s resources.

    The label set varies between Pod-template labels (no task-runner
    tag) and Job/Secret metadata (with task-runner tag). Container
    names are truncated to 63 characters to stay within the
    Kubernetes label-value length limit.
    """
    labels = {
        _K8S_LABEL_PART_OF: _SHIFTER_PART_OF_VALUE,
        _K8S_LABEL_COMPONENT: container_name[:63],
    }
    if include_task_runner:
        labels[_SHIFTER_LABEL_TASK_RUNNER] = _SHIFTER_TASK_RUNNER_GCP
    return labels


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


def _is_provisioner_task(container_name: str) -> bool:
    """Return True if the Job being built is the provisioner task.

    Hardening from issue #1103 (read-only root filesystem, writable workspace
    volume, drop-ALL capabilities, etc.) is provisioner-specific. CMS
    experiments and any future shared-runner caller keep their current,
    less-prescribed contract until the runner protocol grows a per-task
    runtime profile parameter.
    """
    return container_name == PROVISIONER_CONTAINER_NAME


def _job_condition_reason(status: object) -> str | None:
    """Return the message/reason of the first Failed/Complete Job condition, if any."""
    for condition in getattr(status, "conditions", None) or []:
        if getattr(condition, "type", "") in {"Failed", "Complete"}:
            return getattr(condition, "message", None) or getattr(condition, "reason", None)
    return None


def _derive_job_state(*, active: int, failed: int, succeeded: int) -> str:
    """Map active/failed/succeeded Job counts to a coarse ECS-style task state."""
    if succeeded > 0:
        return "SUCCEEDED"
    if failed > 0:
        return "FAILED"
    return "RUNNING" if active > 0 else "SUBMITTED"


class GCPTaskRunner:
    """Kubernetes Job implementation of TaskRunner protocol.

    The generic TaskRunner interface remains ECS-shaped in existing call sites.
    For GCP:

    - ``cluster`` is interpreted as the Kubernetes namespace.
    - ``task_definition`` is interpreted as the container image to run.
    - ``command`` is passed as container args so the image ENTRYPOINT is kept.
    """

    def _load_kubernetes_api(self) -> tuple[object, object, object, type[Exception]]:
        try:
            kubernetes = importlib.import_module("kubernetes")
        except ImportError as e:
            raise CloudTaskError("GCP task runner support requires kubernetes") from e

        config = kubernetes.config
        config_exception = getattr(getattr(config, "config_exception", None), "ConfigException", Exception)

        try:
            if os.environ.get("KUBERNETES_SERVICE_HOST"):
                try:
                    config.load_incluster_config()
                except config_exception:
                    config.load_kube_config()
            else:
                config.load_kube_config()
        except Exception as e:
            raise CloudTaskError(f"Failed to load Kubernetes client configuration ({type(e).__name__})") from e

        client = kubernetes.client
        api_exception = getattr(getattr(client, "exceptions", None), "ApiException", Exception)
        return client.BatchV1Api(), client.CoreV1Api(), client, api_exception

    @staticmethod
    def _job_sensitive_secret_name(job: object) -> str | None:
        """Return the per-Job sensitive-env Secret referenced by a Job."""
        pod_spec = getattr(getattr(getattr(job, "spec", None), "template", None), "spec", None)
        for container in getattr(pod_spec, "containers", None) or []:
            for entry in getattr(container, "env", None) or []:
                secret_ref = getattr(getattr(entry, "value_from", None), "secret_key_ref", None)
                name = getattr(secret_ref, "name", None)
                if isinstance(name, str) and name.startswith("pulumi-provisioner-secrets-"):
                    return name
        return None

    def _reconcile_observed_job(
        self,
        *,
        job: object,
        apis: _KubernetesApis,
        namespace: str,
        job_name: str,
    ) -> None:
        """Finish Secret ownership when create-or-observe finds an accepted Job."""
        secret_name = self._job_sensitive_secret_name(job)
        if secret_name is None:
            return
        self._install_owner_reference_or_unwind(
            apis=apis,
            namespace=namespace,
            job_name=job_name,
            job_uid=getattr(getattr(job, "metadata", None), "uid", None),
            secret_name=secret_name,
            unwind_on_failure=False,
        )

    def _validate_observed_job(
        self,
        job: object,
        identity: _JobIdentity,
    ) -> None:
        """Reject a deterministic-name collision that is not this exact intent."""
        metadata = getattr(job, "metadata", None)
        annotations = getattr(metadata, "annotations", None) or {}
        pod_spec = getattr(getattr(getattr(job, "spec", None), "template", None), "spec", None)
        containers = getattr(pod_spec, "containers", None) or []
        container = containers[0] if len(containers) == 1 else None
        observed = {
            "job_name": getattr(metadata, "name", None),
            "task_identity": annotations.get(_SHIFTER_ANNOTATION_TASK_IDENTITY),
            "service_account_name": getattr(pod_spec, "service_account_name", None) or "",
            "container_name": getattr(container, "name", None),
            "image": getattr(container, "image", None),
            "command": list(getattr(container, "args", None) or []),
            "secret_name": self._job_sensitive_secret_name(job),
        }
        expected = {
            "job_name": identity.job_name,
            "task_identity": identity.task_identity,
            "service_account_name": identity.service_account_name,
            "container_name": identity.container_name,
            "image": identity.image,
            "command": identity.command,
            "secret_name": identity.secret_name,
        }
        if observed != expected:
            raise CloudTaskError("Observed Kubernetes Job does not match the reserved provisioner launch identity")

    @staticmethod
    def _read_idempotent_job(
        batch_api: object,
        api_exception: type[Exception],
        namespace: str,
        job_name: str,
    ) -> object | None:
        try:
            return _api_call(
                batch_api,
                "read_namespaced_job",
                name=job_name,
                namespace=namespace,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except api_exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise

    def _accept_observed_job(self, launch: _JobLaunch, observed: object) -> tuple[object, str, str | None, bool]:
        """Validate and reconcile a reserved Job found after a create attempt."""
        identity = launch.identity
        if identity is None:
            self._cleanup_sensitive_secret(launch.apis.core, launch.secret_name, launch.namespace)
            raise CloudTaskError("Deterministic Job recovery requires a task identity")
        try:
            self._validate_observed_job(observed, identity)
        except CloudTaskError:
            self._cleanup_sensitive_secret(launch.apis.core, launch.secret_name, launch.namespace)
            raise
        observed_secret_name = self._job_sensitive_secret_name(observed)
        if launch.secret_name != observed_secret_name:
            self._cleanup_sensitive_secret(launch.apis.core, launch.secret_name, launch.namespace)
        self._reconcile_observed_job(
            job=observed,
            apis=launch.apis,
            namespace=launch.namespace,
            job_name=identity.job_name,
        )
        return observed, identity.job_name, observed_secret_name, True

    def _observe_reserved_job(self, launch: _JobLaunch) -> object | None:
        """Read the deterministic Job associated with a launch, when available."""
        identity = launch.identity
        if identity is None:
            return None
        return self._read_idempotent_job(
            launch.apis.batch,
            launch.apis.exception,
            launch.namespace,
            identity.job_name,
        )

    def _create_or_observe_job(
        self,
        launch: _JobLaunch,
    ) -> tuple[object, str, str | None, bool]:
        """Create a Job or reconcile the same deterministic Job after ambiguity."""
        try:
            created = _api_call(
                launch.apis.batch,
                "create_namespaced_job",
                namespace=launch.namespace,
                body=launch.job,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as create_exc:
            observed = self._observe_reserved_job(launch)
            if observed is None:
                status = getattr(create_exc, "status", None)
                if launch.identity is None or status in {400, 401, 403, 405, 406, 415, 422}:
                    self._cleanup_sensitive_secret(launch.apis.core, launch.secret_name, launch.namespace)
                raise
            return self._accept_observed_job(launch, observed)

        job_name = getattr(getattr(created, "metadata", None), "name", None)
        if job_name:
            return created, str(job_name), launch.secret_name, False
        observed = self._observe_reserved_job(launch)
        if observed is not None:
            return self._accept_observed_job(launch, observed)
        self._cleanup_sensitive_secret(launch.apis.core, launch.secret_name, launch.namespace)
        raise CloudTaskError("Kubernetes API did not return a Job name")

    def _build_env(
        self,
        client: Any,
        env_overrides: dict[str, str] | None,
        sensitive_secret_name: str | None = None,
    ) -> list[Any] | None:
        """Build the env-var list for the Job container.

        Issue #1185 — sensitive provisioner values must NOT appear as
        literal ``value=`` env vars on the Pod spec. They are routed
        through ``valueFrom.secretKeyRef`` pointing at an ephemeral
        per-Job Kubernetes Secret. ``sensitive_secret_name`` MUST be
        provided when any key in ``env_overrides`` classifies as
        sensitive (per ``sensitive_env.split_env``); the caller
        (``run_task``) creates the Secret before the Job is submitted.
        """
        if not env_overrides:
            return None
        sensitive, plain = split_env(env_overrides)
        if sensitive and not sensitive_secret_name:
            raise CloudTaskError("GCP task runner: sensitive env vars present but no Secret was created")
        entries: list[Any] = []
        for name, value in sorted(plain.items()):
            entries.append(client.V1EnvVar(name=name, value=value))
        for name in sorted(sensitive.keys()):
            entries.append(
                client.V1EnvVar(
                    name=name,
                    value_from=client.V1EnvVarSource(
                        secret_key_ref=client.V1SecretKeySelector(
                            name=sensitive_secret_name,
                            key=name,
                        ),
                    ),
                )
            )
        return entries

    def _build_sensitive_secret(
        self,
        client: Any,
        secret_name: str,
        sensitive_env: dict[str, str],
        container_name: str,
    ) -> Any:
        """Build the ephemeral Secret manifest for the sensitive env vars.

        The Secret name is derived from ``container_name`` and the
        launch-intent identity when one is available, so a redelivery
        can safely recreate or repair the same object. Labels mirror
        the Job's labels so operators can correlate the Secret with
        the Job in kubectl. ``string_data`` carries plaintext values
        which the apiserver base64-encodes into ``data`` on write.
        """
        labels = _shifter_resource_labels(container_name, include_task_runner=True)
        labels["shifter.dev/secret-purpose"] = "provisioner-sensitive-env"
        return client.V1Secret(
            api_version="v1",
            kind="Secret",
            type="Opaque",
            metadata=client.V1ObjectMeta(name=secret_name, labels=labels),
            string_data=dict(sensitive_env),
        )

    @staticmethod
    def _build_secret_name(container_name: str, task_identity: str | None = None) -> str:
        """Derive a unique Secret name for a provisioner Job.

        Kubernetes object names cap at 253 characters and follow DNS
        subdomain rules. We keep the prefix under 50 characters and
        append either a stable 16-character digest of the launch intent
        or, for legacy callers, a fresh 12-character UUID slice.
        """
        # Lowercase + replace any non-DNS-subdomain characters.
        prefix = container_name.lower().replace("_", "-")
        # Trim to a generous bound; the runtime contract is just "unique".
        prefix = prefix[:40].rstrip("-") or "provisioner"
        suffix = sha256(task_identity.encode("utf-8")).hexdigest()[:16] if task_identity else uuid.uuid4().hex[:12]
        return f"{prefix}-secrets-{suffix}"

    def _ensure_sensitive_secret(
        self,
        *,
        apis: _KubernetesApis,
        namespace: str,
        secret_name: str,
        sensitive_env: dict[str, str],
        container_name: str,
        task_identity: str | None,
    ) -> None:
        """Create a per-intent Secret, or recover it after an ambiguous create."""
        secret_body = self._build_sensitive_secret(apis.client, secret_name, sensitive_env, container_name)
        try:
            _api_call(
                apis.core,
                "create_namespaced_secret",
                namespace=namespace,
                body=secret_body,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except apis.exception as exc:
            if task_identity is None or getattr(exc, "status", None) != 409:
                raise
            # A deterministic per-intent Secret can remain after an ambiguous
            # create response. Reassert its exact payload/labels and remove any
            # stale owner before attaching it to the newly accepted Job.
            patch_body = {
                "metadata": {
                    "labels": getattr(getattr(secret_body, "metadata", None), "labels", None) or {},
                    "ownerReferences": [],
                },
                "stringData": dict(sensitive_env),
                "type": "Opaque",
            }
            _api_call(
                apis.core,
                "patch_namespaced_secret",
                name=secret_name,
                namespace=namespace,
                body=patch_body,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )

    def _build_container_security_context(self, client: Any) -> Any:
        # Issue #1103: lock the provisioner Job's writable surface to the
        # explicit volumes built below. ALL capabilities dropped, no privilege
        # escalation, non-root, read-only root FS.
        return client.V1SecurityContext(
            read_only_root_filesystem=True,
            run_as_non_root=True,
            run_as_user=_PROVISIONER_RUN_AS_UID,
            run_as_group=_PROVISIONER_RUN_AS_GID,
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
        )

    def _build_pod_security_context(self, client: Any) -> Any:
        # seccompProfile=RuntimeDefault matches the platform's worker-engine
        # baseline and is required for restricted Pod Security Standard
        # admission (ADR-006). fsGroup=1000 makes the kubelet chown mounted
        # emptyDir volumes to the runtime group so the non-root container can
        # write to them without an init-chown or fsGroupChangePolicy=Always
        # (which would also re-chown read-only mounts on every start).
        return client.V1PodSecurityContext(
            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
            fs_group=_PROVISIONER_RUN_AS_GID,
            fs_group_change_policy="OnRootMismatch",
        )

    def _build_writable_volumes(self, client: Any) -> list[Any]:
        volumes = []
        for name, _mount_path, medium, size_limit in _PROVISIONER_WRITABLE_MOUNTS:
            empty_dir_kwargs: dict[str, Any] = {}
            if medium:
                empty_dir_kwargs["medium"] = medium
            if size_limit:
                empty_dir_kwargs["size_limit"] = size_limit
            volumes.append(
                client.V1Volume(name=name, empty_dir=client.V1EmptyDirVolumeSource(**empty_dir_kwargs)),
            )
        return volumes

    def _build_container_volume_mounts(self, client: Any) -> list[Any]:
        return [
            client.V1VolumeMount(name=name, mount_path=mount_path)
            for name, mount_path, _medium, _size_limit in _PROVISIONER_WRITABLE_MOUNTS
        ]

    def _build_container(
        self,
        client: Any,
        container_name: str,
        image: str,
        command: list[str],
        env: list[Any] | None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "name": container_name,
            "image": image,
            "args": command,
            "env": env,
            "image_pull_policy": getattr(settings, "ENGINE_TASK_IMAGE_PULL_POLICY", "IfNotPresent"),
        }
        if _is_provisioner_task(container_name):
            kwargs["security_context"] = self._build_container_security_context(client)
            kwargs["volume_mounts"] = self._build_container_volume_mounts(client)
        return client.V1Container(**kwargs)

    def _build_job(
        self,
        client: Any,
        image: str,
        container_name: str,
        command: list[str],
        env: list[Any] | None,
        task_identity: str | None = None,
    ) -> Any:
        pod_spec_kwargs: dict[str, Any] = {
            "containers": [self._build_container(client, container_name, image, command, env)],
            "restart_policy": "Never",
        }
        if _is_provisioner_task(container_name):
            pod_spec_kwargs["security_context"] = self._build_pod_security_context(client)
            pod_spec_kwargs["volumes"] = self._build_writable_volumes(client)
            pod_spec_kwargs["automount_service_account_token"] = False
        pod_spec = client.V1PodSpec(**pod_spec_kwargs)

        service_account_name = getattr(settings, "ENGINE_TASK_SERVICE_ACCOUNT_NAME", "")
        if service_account_name:
            pod_spec.service_account_name = service_account_name

        metadata_kwargs: dict[str, Any] = {
            "labels": _shifter_resource_labels(container_name, include_task_runner=True),
            "annotations": {"shifter.dev/task-image": image},
        }
        if task_identity:
            metadata_kwargs["name"] = build_idempotent_job_name(container_name, task_identity)
            metadata_kwargs["annotations"][_SHIFTER_ANNOTATION_TASK_IDENTITY] = task_identity
        else:
            metadata_kwargs["generate_name"] = build_job_generate_name(container_name, command)
        metadata = client.V1ObjectMeta(
            **metadata_kwargs,
        )

        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(
                labels=_shifter_resource_labels(container_name, include_task_runner=False),
            ),
            spec=pod_spec,
        )

        spec = client.V1JobSpec(
            template=template,
            backoff_limit=getattr(settings, "ENGINE_TASK_BACKOFF_LIMIT", 0),
            ttl_seconds_after_finished=getattr(settings, "ENGINE_TASK_TTL_SECONDS_AFTER_FINISHED", 3600),
        )

        return client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=metadata,
            spec=spec,
        )

    def _extract_stopped_reason(self, core_api: Any, namespace: str, job_name: str) -> str | None:
        try:
            pods = core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}",
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug("get_task_status: failed to list pods for job=%s", job_name, exc_info=True)
            return None

        for pod in getattr(pods, "items", []):
            container_statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
            for container_status in container_statuses:
                state = getattr(container_status, "state", None)
                terminated = getattr(state, "terminated", None)
                if terminated:
                    return getattr(terminated, "message", None) or getattr(terminated, "reason", None)
        return None

    def _read_job_status(self, batch_api: Any, namespace: str, job_name: str, api_exception: type[Exception]) -> Any:
        try:
            return batch_api.read_namespaced_job_status(
                name=job_name,
                namespace=namespace,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except api_exception as e:
            if getattr(e, "status", None) == 404:
                return None
            raise

    def _build_status_payload(self, status: Any, core_api: Any, namespace: str, job_name: str) -> dict[str, Any]:
        active = int(getattr(status, "active", 0) or 0)
        failed = int(getattr(status, "failed", 0) or 0)
        succeeded = int(getattr(status, "succeeded", 0) or 0)
        started_at = getattr(status, "start_time", None)
        stopped_at = getattr(status, "completion_time", None)

        stopped_reason = _job_condition_reason(status)
        state = _derive_job_state(active=active, failed=failed, succeeded=succeeded)

        if state in {"SUCCEEDED", "FAILED"} and not stopped_reason:
            stopped_reason = self._extract_stopped_reason(core_api, namespace, job_name)

        return {
            "task_id": f"{namespace}/{job_name}",
            "status": state,
            "desired_status": "RUNNING" if state in {"SUBMITTED", "RUNNING"} else "COMPLETED",
            "started_at": started_at,
            "stopped_at": stopped_at,
            "stopped_reason": stopped_reason,
        }

    def _resolve_run_context(
        self,
        namespace: str,
        image: str,
        command: list[str],
        container_name: str,
        env_overrides: dict[str, str] | None,
        task_identity: str | None,
    ) -> _RunTaskContext:
        """Load Kubernetes clients and derive deterministic launch identities."""
        apis = _KubernetesApis(*self._load_kubernetes_api())
        service_account_name = str(getattr(settings, "ENGINE_TASK_SERVICE_ACCOUNT_NAME", "") or "")
        sensitive_env, _plain_env = split_env(env_overrides or {})
        secret_name = self._build_secret_name(container_name, task_identity) if sensitive_env else None
        identity = None
        if task_identity:
            identity = _JobIdentity(
                job_name=build_idempotent_job_name(container_name, task_identity),
                task_identity=task_identity,
                image=image,
                command=command,
                container_name=container_name,
                service_account_name=service_account_name,
                secret_name=secret_name,
            )
        return _RunTaskContext(
            apis=apis,
            namespace=namespace,
            image=image,
            command=command,
            container_name=container_name,
            env_overrides=env_overrides,
            task_identity=task_identity,
            identity=identity,
            sensitive_env=sensitive_env,
            secret_name=secret_name,
        )

    def _existing_task_ref(self, context: _RunTaskContext) -> str | None:
        """Reconcile and return an already accepted deterministic Job."""
        identity = context.identity
        if identity is None:
            return None
        observed = self._read_idempotent_job(
            context.apis.batch,
            context.apis.exception,
            context.namespace,
            identity.job_name,
        )
        if observed is None:
            return None
        self._validate_observed_job(observed, identity)
        if context.sensitive_env and context.secret_name is not None:
            self._ensure_sensitive_secret(
                apis=context.apis,
                namespace=context.namespace,
                secret_name=context.secret_name,
                sensitive_env=context.sensitive_env,
                container_name=context.container_name,
                task_identity=context.task_identity,
            )
        self._reconcile_observed_job(
            job=observed,
            apis=context.apis,
            namespace=context.namespace,
            job_name=identity.job_name,
        )
        return f"{context.namespace}/{identity.job_name}"

    def _build_launch_job(self, context: _RunTaskContext) -> object:
        """Create sensitive state and build the corresponding Job manifest."""
        if context.sensitive_env:
            if context.secret_name is None:  # pragma: no cover - derived above
                raise CloudTaskError("GCP task runner failed to derive a Secret name")
            self._ensure_sensitive_secret(
                apis=context.apis,
                namespace=context.namespace,
                secret_name=context.secret_name,
                sensitive_env=context.sensitive_env,
                container_name=context.container_name,
                task_identity=context.task_identity,
            )
        try:
            env = self._build_env(
                context.apis.client,
                context.env_overrides,
                sensitive_secret_name=context.secret_name,
            )
            return self._build_job(
                context.apis.client,
                context.image,
                context.container_name,
                context.command,
                env,
                task_identity=context.task_identity,
            )
        except Exception:
            self._cleanup_sensitive_secret(context.apis.core, context.secret_name, context.namespace)
            raise

    def _submit_launch_job(self, context: _RunTaskContext, job: object) -> str:
        """Submit a built Job and finish ownership of any sensitive Secret."""
        launch = _JobLaunch(
            apis=context.apis,
            namespace=context.namespace,
            job=job,
            identity=context.identity,
            secret_name=context.secret_name,
        )
        created, job_name, effective_secret_name, owner_reconciled = self._create_or_observe_job(launch)
        if effective_secret_name is not None and not owner_reconciled:
            self._install_owner_reference_or_unwind(
                apis=context.apis,
                namespace=context.namespace,
                job_name=job_name,
                job_uid=getattr(getattr(created, "metadata", None), "uid", None),
                secret_name=effective_secret_name,
            )
        task_ref = f"{context.namespace}/{job_name}"
        logger.info("run_task: started job=%s image=%s", task_ref, context.image)
        return task_ref

    def _run_task(self, context: _RunTaskContext) -> str:
        """Reconcile an existing Job or submit a newly built manifest."""
        existing_ref = self._existing_task_ref(context)
        if existing_ref is not None:
            return existing_ref
        return self._submit_launch_job(context, self._build_launch_job(context))

    def run_task(
        self,
        task_definition: str,
        cluster: str,
        command: list[str],
        container_name: str,
        env_overrides: dict[str, str] | None = None,
        network_config: dict[str, Any] | None = None,
        task_identity: str | None = None,
    ) -> str | None:
        del network_config  # Networking is handled by the cluster and namespace policies.
        logger.debug("run_task: task_definition=%s cluster=%s", task_definition, cluster)

        namespace = cluster
        image = task_definition
        if not namespace:
            raise CloudTaskError("GCP task runner requires a Kubernetes namespace in ENGINE_TASK_CLUSTER")
        if not image:
            raise CloudTaskError("GCP task runner requires a container image in ENGINE_TASK_DEFINITION")

        try:
            context = self._resolve_run_context(
                namespace,
                image,
                command,
                container_name,
                env_overrides,
                task_identity,
            )
            return self._run_task(context)
        except CloudTaskError:
            raise
        except Exception as e:
            logger.exception(
                "run_task: failed task_definition=%s error_type=%s",
                task_definition,
                type(e).__name__,
            )
            raise CloudTaskError(f"Failed to create Kubernetes Job ({type(e).__name__})") from e

    @staticmethod
    def _owner_ref_to_dict(owner_ref: _OwnerReference) -> dict[str, object]:
        """Convert a V1OwnerReference to the camelCase dict shape the
        apiserver expects in a strategic-merge patch body."""
        return {
            "apiVersion": owner_ref.api_version,
            "kind": owner_ref.kind,
            "name": owner_ref.name,
            "uid": owner_ref.uid,
            "controller": owner_ref.controller,
            "blockOwnerDeletion": owner_ref.block_owner_deletion,
        }

    @staticmethod
    def _cleanup_sensitive_secret(core_api: Any, secret_name: str | None, namespace: str) -> None:
        """Delete an orphan sensitive-env Secret. Best-effort: log on
        failure so the operator can clean up manually, but never raise
        — the caller is already on an error path."""
        if not secret_name:
            return
        try:
            core_api.delete_namespaced_secret(
                name=secret_name,
                namespace=namespace,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "run_task: failed to clean up orphan secret_fp=%s namespace=%s",
                safe_log_fingerprint(secret_name),
                namespace,
                exc_info=True,
            )

    def _install_owner_reference_or_unwind(
        self,
        *,
        apis: _KubernetesApis,
        namespace: str,
        job_name: str,
        job_uid: str | None,
        secret_name: str,
        unwind_on_failure: bool = True,
    ) -> None:
        """Patch the Secret's ownerReferences. If the patch fails (or
        the Job uid was unavailable), delete BOTH the Job and the
        Secret and raise CloudTaskError — the alternative is an
        indefinite orphan Secret with sensitive payload."""
        if not job_uid:
            if unwind_on_failure:
                self._unwind_run(
                    apis.batch,
                    apis.core,
                    namespace,
                    job_name,
                    secret_name,
                    detail="created Job lacks a uid we can use as ownerReference target",
                )
            raise CloudTaskError(
                f"GCP task runner: cannot install Secret ownerReference (Job {job_name} returned no uid)"
            )

        owner_ref = cast(
            _OwnerReference,
            _api_call(
                apis.client,
                "V1OwnerReference",
                api_version="batch/v1",
                kind="Job",
                name=job_name,
                uid=job_uid,
                controller=True,
                block_owner_deletion=True,
            ),
        )
        patch_body = {"metadata": {"ownerReferences": [self._owner_ref_to_dict(owner_ref)]}}
        try:
            _api_call(
                apis.core,
                "patch_namespaced_secret",
                name=secret_name,
                namespace=namespace,
                body=patch_body,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as patch_err:
            if unwind_on_failure:
                self._unwind_run(
                    apis.batch,
                    apis.core,
                    namespace,
                    job_name,
                    secret_name,
                    detail=f"ownerReference patch failed ({type(patch_err).__name__})",
                )
            raise CloudTaskError(
                f"GCP task runner: failed to install Secret ownerReference for Job {job_name} "
                f"({type(patch_err).__name__})"
            ) from patch_err

    @staticmethod
    def _unwind_run(
        batch_api: Any,
        core_api: Any,
        namespace: str,
        job_name: str,
        secret_name: str,
        *,
        detail: str,
    ) -> None:
        """Delete the Job and the Secret we created earlier in this
        run. Each delete is best-effort: we log and continue so the
        caller's exception (raised after we return) carries the
        original cause."""
        logger.warning(
            "run_task: unwinding run for job=%s secret_fp=%s namespace=%s reason=%s",
            job_name,
            safe_log_fingerprint(secret_name),
            namespace,
            detail,
        )
        try:
            batch_api.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "run_task: failed to delete Job during unwind job=%s namespace=%s",
                job_name,
                namespace,
                exc_info=True,
            )
        try:
            core_api.delete_namespaced_secret(
                name=secret_name,
                namespace=namespace,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "run_task: failed to delete Secret during unwind secret_fp=%s namespace=%s",
                safe_log_fingerprint(secret_name),
                namespace,
                exc_info=True,
            )

    def get_task_status(self, cluster: str, task_id: str) -> dict[str, Any] | None:
        logger.debug("get_task_status: cluster=%s task_id=%s", cluster, task_id)
        if not task_id:
            return None

        namespace, job_name = parse_job_task_id(task_id, cluster)
        if not namespace or not job_name:
            return None

        try:
            batch_api, core_api, _client, api_exception = self._load_kubernetes_api()
            job = self._read_job_status(batch_api, namespace, job_name, api_exception)
            if job is None:
                return None
            return self._build_status_payload(getattr(job, "status", None), core_api, namespace, job_name)
        except CloudTaskError:
            raise
        except Exception as e:
            logger.exception("get_task_status: failed task_id=%s error_type=%s", task_id, type(e).__name__)
            raise CloudTaskError(f"Failed to get Kubernetes Job status ({type(e).__name__})") from e
