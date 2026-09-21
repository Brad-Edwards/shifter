"""Neutral binding for SQL-authoritative authorization descendant inventory."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .contracts import TargetRef


class AuthorizationDescendantResolutionError(RuntimeError):
    """The authoritative descendant inventory is unavailable or exceeds its bound."""


AuthorizationDescendantResolver = Callable[[Sequence[int], int], tuple[TargetRef, ...]]
_REQUIRED_RESOLVERS = {"ctf.events": "event", "engine.ranges": "range"}
_resolvers: dict[str, AuthorizationDescendantResolver] = {}


def bind_authorization_descendant_resolver(name: str, resolver: AuthorizationDescendantResolver) -> None:
    """Bind one SQL-owning domain's descendant resolver once."""
    if name not in _REQUIRED_RESOLVERS or not callable(resolver):
        raise AuthorizationDescendantResolutionError("Authorization descendant resolver binding is invalid")
    existing = _resolvers.get(name)
    if existing is not None and existing is not resolver:
        raise AuthorizationDescendantResolutionError(f"A different authorization descendant resolver owns {name}")
    _resolvers[name] = resolver


def resolve_authorization_descendants(workspace_ids: Sequence[int], limit: int) -> tuple[TargetRef, ...]:
    """Return bounded event/range targets from their SQL-owning domains."""
    if set(_resolvers) != set(_REQUIRED_RESOLVERS):
        raise AuthorizationDescendantResolutionError("Authorization descendant resolver is unavailable")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise AuthorizationDescendantResolutionError("Authorization descendant limit is invalid")
    descendants: list[TargetRef] = []
    for name in sorted(_resolvers):
        remaining = limit - len(descendants)
        try:
            resolved = _resolvers[name](tuple(workspace_ids), remaining + 1)
        except AuthorizationDescendantResolutionError:
            raise
        except Exception as exc:
            raise AuthorizationDescendantResolutionError("Authorization descendant resolution failed") from exc
        if len(resolved) > remaining or any(item.type != _REQUIRED_RESOLVERS[name] for item in resolved):
            raise AuthorizationDescendantResolutionError("Authorization descendant resolution is invalid")
        descendants.extend(resolved)
    return tuple(descendants)
