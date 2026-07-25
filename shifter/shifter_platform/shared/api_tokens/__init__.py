"""Platform-wide scoped API token authentication (PLAT-102).

This package owns the platform programmatic auth principal: the ``ApiToken``
model, the central scope registry, the DRF authentication class and scope
permission, and the Django-admin management surface. It is the going-forward
principal for the whole platform (PLAT-106), so it lives in ``shared`` where any
app may depend on it without a cross-app import.

The package is kept free of eager audit-subsystem imports at import time; audit
writes delegate to ``shared.audit`` lazily through :mod:`shared.api_tokens.audit`.
"""
