"""Neutral binding for SQL-authoritative authorization descendant inventory."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from shared.identity_scope import ResourceScope

from .contracts import TargetRef


class AuthorizationDescendantResolutionError(RuntimeError):
    """The authoritative descendant inventory is unavailable or exceeds its bound."""


AuthorizationDescendantResolver = Callable[[Sequence[int], int], tuple[TargetRef, ...]]
_REQUIRED_RESOLVERS = {"ctf.events": "event", "engine.ranges": "range"}
_resolvers: dict[str, AuthorizationDescendantResolver] = {}
AuthorizationEventScopeResolver = Callable[[UUID], ResourceScope]
AuthorizationNonworkspaceEventResolver = Callable[[ResourceScope, int], tuple[tuple[TargetRef, ResourceScope], ...]]
_event_scope_resolver: AuthorizationEventScopeResolver | None = None
_nonworkspace_event_resolver: AuthorizationNonworkspaceEventResolver | None = None


def bind_authorization_event_inventory(
    scope_resolver: AuthorizationEventScopeResolver,
    nonworkspace_resolver: AuthorizationNonworkspaceEventResolver,
) -> None:
    """Bind CTF-owned exact event scope and account/organization event inventory."""
    global _event_scope_resolver, _nonworkspace_event_resolver
    if not callable(scope_resolver) or not callable(nonworkspace_resolver):
        raise AuthorizationDescendantResolutionError("Authorization event inventory binding is invalid")
    if (_event_scope_resolver is not None and _event_scope_resolver is not scope_resolver) or (
        _nonworkspace_event_resolver is not None and _nonworkspace_event_resolver is not nonworkspace_resolver
    ):
        raise AuthorizationDescendantResolutionError("A different resolver owns the event inventory")
    _event_scope_resolver = scope_resolver
    _nonworkspace_event_resolver = nonworkspace_resolver


def resolve_authorization_event_scope(event_uuid: UUID) -> ResourceScope:
    """Resolve an event's exact SQL ancestry, failing closed on missing data."""
    if _event_scope_resolver is None:
        raise AuthorizationDescendantResolutionError("Authorization event inventory is unavailable")
    try:
        scope = _event_scope_resolver(event_uuid)
    except Exception as exc:
        raise AuthorizationDescendantResolutionError("Authorization event scope is unavailable") from exc
    if not isinstance(scope, ResourceScope):
        raise AuthorizationDescendantResolutionError("Authorization event scope is invalid")
    return scope


def resolve_authorization_nonworkspace_events(
    parent: ResourceScope, limit: int
) -> tuple[tuple[TargetRef, ResourceScope], ...]:
    """Return bounded events placed directly in an account or organization."""
    if _nonworkspace_event_resolver is None:
        raise AuthorizationDescendantResolutionError("Authorization event inventory is unavailable")
    if (
        not isinstance(parent, ResourceScope)
        or parent.workspace_uuid is not None
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 0
    ):
        raise AuthorizationDescendantResolutionError("Authorization event inventory request is invalid")
    try:
        rows = _nonworkspace_event_resolver(parent, limit + 1)
    except Exception as exc:
        raise AuthorizationDescendantResolutionError("Authorization event inventory is unavailable") from exc
    if len(rows) > limit or any(
        target.type != "event"
        or scope.kind != "account"
        or scope.workspace_uuid is not None
        or (parent.kind == "account" and scope.account_uuid != parent.account_uuid)
        or (parent.organization_uuid is not None and scope.organization_uuid != parent.organization_uuid)
        for target, scope in rows
    ):
        raise AuthorizationDescendantResolutionError("Authorization event inventory is invalid")
    return rows


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
