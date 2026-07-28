"""Shifter's configuration-bound RAES realization contract.

The public RAES ``RealizerConfigurationModel`` is the independent apply-time
evidence surface for the backend. It declares the account features and resource
families this Shifter configuration genuinely realizes, while the backend
manifest remains the authoring capability surface. The common validate/apply
path consults this contract before dispatch so a manifest over-claim cannot
become an accepted plan.
"""

from __future__ import annotations

from raes_contracts.realization_envelope import (
    RealizerConfigurationModel,
    realizer_configuration_digest,
)

_CONFIGURATION = {
    "mode": "shifter-provider-native",
    "architecture": "x86_64",
    "image_policy": "raes-image-registry",
    "network_policy": "isolated-range-cell",
    "supported_node_types": ["switch", "vm"],
    "supported_os_families": ["linux", "windows"],
    "supported_content_types": ["directory", "file"],
    "supported_account_features": ["auth_method", "disabled", "groups", "home", "shell", "spn"],
    "supported_domain_profiles": ["active_directory"],
    "supports_acls": True,
    "memory_mib": {"minimum": 512, "maximum": None},
    "vcpus": {"minimum": 1, "maximum": None},
}


def create_shifter_realizer_configuration() -> RealizerConfigurationModel:
    """Return Shifter's validated, digest-bound realizer configuration."""
    digest = realizer_configuration_digest(_CONFIGURATION)
    return RealizerConfigurationModel(configuration_digest=digest, **_CONFIGURATION)


SHIFTER_REALIZER_CONFIGURATION = create_shifter_realizer_configuration()
REALIZED_ACCOUNT_FEATURES = frozenset(SHIFTER_REALIZER_CONFIGURATION.supported_account_features)

__all__ = [
    "REALIZED_ACCOUNT_FEATURES",
    "SHIFTER_REALIZER_CONFIGURATION",
    "create_shifter_realizer_configuration",
]
