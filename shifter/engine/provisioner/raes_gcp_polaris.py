"""Polaris-specific post-provision step for the RAES-native GCE apply path.

Extracted from ``raes_gcp_apply`` to isolate the polaris (docker-host +
prepromoted-domain-controller) concern and keep the apply module within the
file-size budget. The apply pass calls :func:`_run_polaris_post_provision` after
the range's guests exist.
"""

from __future__ import annotations

from config import GCE_BOOTSTRAP_POLARIS_HOST, GCE_BOOTSTRAP_PREPROMOTED_DC
from gcp_range_cell_types import ResourceDict
from instance_setup import _set_attacker_container_password_after_bootstrap
from polaris_bootstrap import _run_polaris_range_bootstrap
from raes_gcp_plan import RaesGcePlanError


def _run_polaris_post_provision(instance_outputs: list[ResourceDict], range_id: int) -> None:
    """Materialize the polaris compose stack on any polaris-docker-host guest.

    The RAES-path counterpart of the legacy range-cell polaris post-provision
    (``instance_orchestrator``): the polaris-vm image ships the a0-a16 docker
    compose stack baked with a bake-time DC IP and a throwaway kali key, so after
    the guests exist we reuse the reviewed ``PolarisRangeBootstrapPlan`` to
    rewrite the compose override with THIS range's actual DC IP (statically
    assigned at plan time) and per-instance participant key, then force-recreate
    the dns + a14-kali containers and set the per-range attacker password. A
    polaris-docker-host guest requires its prepromoted-domain-controller peer to
    supply the DC IP. A no-op for ranges with no polaris host.
    """
    hosts = [out for out in instance_outputs if out.get("gcp_bootstrap_capability") == GCE_BOOTSTRAP_POLARIS_HOST]
    if not hosts:
        return
    dc = next(
        (out for out in instance_outputs if out.get("gcp_bootstrap_capability") == GCE_BOOTSTRAP_PREPROMOTED_DC),
        None,
    )
    if dc is None:
        raise RaesGcePlanError(
            "a polaris-docker-host range requires a prepromoted-domain-controller instance to supply the DC IP"
        )
    dc_ip = str(dc.get("private_ip") or "")
    for host in hosts:
        instance_id = str(host["instance_id"])
        _run_polaris_range_bootstrap(
            instance_data=host,
            instance_id=instance_id,
            dc_ip=dc_ip,
            public_key=str(host.get("public_key") or ""),
            range_id=range_id,
        )
        _set_attacker_container_password_after_bootstrap(
            instance_data=host,
            instance_id=instance_id,
            container_name="a14-kali",
            ssh_user="kali",
        )
