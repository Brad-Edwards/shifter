"""Provider-agnostic cloud exceptions for the provisioner.

Mirrors shared/cloud/exceptions.py but without Django dependency.
"""

from typing import Any


class CloudError(Exception):
    """Base exception for all cloud provider operations."""


class CloudProviderNotImplementedError(CloudError):
    """Raised when a requested cloud provider has no adapter or lacks a capability.

    The supported-provider list is derived from the ``installation`` registry
    (the single source of truth, PLAT-2005) rather than hardcoded here, so it
    can never drift from what the registry actually declares.
    """

    def __init__(self, provider: str, capability: Any = None) -> None:
        # Lazy import: avoids import-time cost and a circular import, since
        # ``config.resolve_cloud_provider`` (which raises this) is what
        # ``cloud`` resolves the active provider through.
        from installation.registry import KNOWN_BACKENDS

        supported = ", ".join(sorted(KNOWN_BACKENDS))
        detail = ""
        if capability is not None:
            detail = f" (missing capability: {getattr(capability, 'name', capability)})"
        super().__init__(f"Cloud provider '{provider}' is not implemented{detail}. Supported providers: {supported}")
        self.provider = provider
        self.capability = capability


class CloudEventBusError(CloudError):
    """Error during event publishing operations."""


class CloudConfigStoreError(CloudError):
    """Error during config/parameter retrieval operations."""


class CloudDBAuthError(CloudError):
    """Error during database auth token generation."""


class CloudStorageError(CloudError):
    """Error during object storage operations."""


class CloudSecretsError(CloudError):
    """Error during secrets retrieval operations."""


class CloudNetworkInventoryError(CloudError):
    """Error during network inventory or subnet exhaustion alert operations."""
