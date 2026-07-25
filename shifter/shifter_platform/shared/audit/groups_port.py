"""Cognito-groups provider port and startup binding (neutral contracts layer).

Mirrors ``shared.audit.port``: the audit-read Cognito-group permission
(``shared.api.permissions.HasAuditLogCognitoGroup``, via
``shared.audit.access``) depends on this neutral protocol instead of importing
the ``management`` domain directly. A direct ``shared -> management`` import
is forbidden by the layer contract (``.importlinter`` /
``scripts/check_layer_imports/layer_imports.yaml``: ``shared`` is
``support_contracts`` and must not import a domain package). The concrete
provider -- which resolves the Cognito groups last captured on a user's
profile via ``management.services.get_user_profile`` -- is bound once at
startup (``config.apps.PortalConfig.ready``), the same seam
``config.cognito_groups`` already uses to persist groups. A missing or
conflicting binding is a startup configuration error, never a silent
"no groups" fallback (#1374 fix-forward).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class CognitoGroupsProviderBindingError(RuntimeError):
    """Raised when the Cognito-groups provider binding is missing or conflicting."""


@runtime_checkable
class CognitoGroupsProvider(Protocol):
    """Resolves the Cognito groups last captured for a user's profile."""

    def groups_for_user(self, user: User) -> list[str]:
        """Return the Cognito groups captured for ``user``, or an empty list."""
        ...


_provider: CognitoGroupsProvider | None = None


def bind_cognito_groups_provider(provider: CognitoGroupsProvider) -> None:
    """Bind the process-wide Cognito-groups provider.

    Idempotent for the same instance so a re-run of the startup hook is safe.
    Binding a *different* provider while one is already bound is a
    configuration error (fail closed), not a silent replacement.
    """
    global _provider
    if _provider is not None and _provider is not provider:
        raise CognitoGroupsProviderBindingError(
            "A Cognito-groups provider is already bound to a different implementation"
        )
    _provider = provider


def get_cognito_groups_provider() -> CognitoGroupsProvider:
    """Return the bound Cognito-groups provider, or raise if never bound."""
    if _provider is None:
        raise CognitoGroupsProviderBindingError(
            "No Cognito-groups provider bound; bind one at startup (config.apps.PortalConfig.ready)"
        )
    return _provider


def reset_cognito_groups_provider() -> None:
    """Clear the binding. Test-only; production binds once at startup."""
    global _provider
    _provider = None
