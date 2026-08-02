"""The injected profile that carries provider-variable Kubernetes task wiring.

The neutral Kubernetes task runner (#1824) owns only generic Job mechanics. Every
provider-specific choice — the runner label value, the runtime service account
(Workload Identity vs IRSA), image pull/backoff/TTL settings, and the provisioner
hardening posture — is resolved by the provider adapter and passed in as this
single frozen profile. The neutral core reads no Django settings and imports no
provider module.
"""

from __future__ import annotations

from dataclasses import dataclass

# A writable mount: (volume name, mount path, emptyDir medium or None, size limit or None).
WritableMount = tuple[str, str, "str | None", "str | None"]


@dataclass(frozen=True)
class ProvisionerHardeningProfile:
    """Hardening applied to a single named task container.

    The restricted security-context *flags* (drop-ALL capabilities, read-only
    root filesystem, non-root, no privilege escalation, seccomp RuntimeDefault,
    ``automountServiceAccountToken=False``) are the generic restricted posture
    rendered by the neutral core. This profile carries the provider/image-variable
    inputs: which container to harden, the runtime uid/gid, and the explicit
    writable-mount layout that container's image needs.
    """

    container_name: str
    run_as_uid: int
    run_as_gid: int
    writable_mounts: tuple[WritableMount, ...]


@dataclass(frozen=True)
class KubernetesTaskProfile:
    """Provider-injected wiring for one Kubernetes task-runner instance."""

    runner_label_value: str
    service_account_name: str
    image_pull_policy: str
    backoff_limit: int
    ttl_seconds_after_finished: int
    hardening: ProvisionerHardeningProfile | None = None

    def hardening_for(self, container_name: str) -> ProvisionerHardeningProfile | None:
        """Return the hardening profile when it applies to ``container_name``."""
        if self.hardening is not None and container_name == self.hardening.container_name:
            return self.hardening
        return None
