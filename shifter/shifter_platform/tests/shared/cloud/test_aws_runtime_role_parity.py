"""Parity backstop between the installation AWS runtime-role manifest and the platform
task runner's AWS forwarding list (#1826).

``installation.runtime_inventory.AWS_PROVISIONER_FORWARDED_RUNTIME_ENV_KEYS`` declares which
platform runtime-env keys the standalone AWS (EKS) provisioner Job receives. The installation
package is standalone and cannot import the Django platform, so that set is maintained as data.
This test — which lives in the platform suite, where both modules are importable — fails if it
drifts from the authoritative forwarding list ``engine.ecs._AWS_PROVISIONER_ENV_KEYS``.
"""

from __future__ import annotations

from installation.runtime_inventory import AWS_PROVISIONER_FORWARDED_RUNTIME_ENV_KEYS

from engine.ecs import _AWS_PROVISIONER_ENV_KEYS


def test_forwarded_manifest_matches_task_runner_forwarding_list() -> None:
    assert set(_AWS_PROVISIONER_ENV_KEYS) == AWS_PROVISIONER_FORWARDED_RUNTIME_ENV_KEYS, (
        "installation.runtime_inventory.AWS_PROVISIONER_FORWARDED_RUNTIME_ENV_KEYS has drifted from "
        "engine.ecs._AWS_PROVISIONER_ENV_KEYS; update the installation manifest"
    )


def test_forwarding_list_has_no_duplicate_keys() -> None:
    assert len(_AWS_PROVISIONER_ENV_KEYS) == len(set(_AWS_PROVISIONER_ENV_KEYS))
