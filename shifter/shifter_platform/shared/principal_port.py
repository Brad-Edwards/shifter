"""Neutral fail-closed principal directory binding (ADR-066, #2315)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from shared.identity_scope import PrincipalRef

PrincipalForUser = Callable[[Any], PrincipalRef]
PrincipalResolver = Callable[[PrincipalRef], PrincipalRef]
PrincipalUuidResolver = Callable[[UUID], PrincipalRef]


class PrincipalResolutionError(RuntimeError):
    """The principal directory is unavailable or rejected an identity."""


_for_user: PrincipalForUser | None = None
_resolver: PrincipalResolver | None = None
_uuid_resolver: PrincipalUuidResolver | None = None


def bind_principal_directory(
    for_user: PrincipalForUser,
    resolver: PrincipalResolver,
    uuid_resolver: PrincipalUuidResolver,
) -> None:
    """Bind the management-owned directory once at composition startup."""
    global _for_user, _resolver, _uuid_resolver
    bindings = ((_for_user, for_user), (_resolver, resolver), (_uuid_resolver, uuid_resolver))
    if any(current is not None and current is not incoming for current, incoming in bindings):
        raise PrincipalResolutionError("A different principal directory is already bound")
    _for_user = for_user
    _resolver = resolver
    _uuid_resolver = uuid_resolver


def principal_for_user(user: Any) -> PrincipalRef:
    if _for_user is None:
        raise PrincipalResolutionError("No principal directory is bound")
    try:
        return _for_user(user)
    except PrincipalResolutionError:
        raise
    except Exception as exc:
        raise PrincipalResolutionError("Principal unavailable") from exc


def resolve_principal(principal: PrincipalRef) -> PrincipalRef:
    if _resolver is None:
        raise PrincipalResolutionError("No principal directory is bound")
    try:
        return _resolver(principal)
    except PrincipalResolutionError:
        raise
    except Exception as exc:
        raise PrincipalResolutionError("Principal unavailable") from exc


def resolve_principal_uuid(principal_uuid: UUID) -> PrincipalRef:
    if _uuid_resolver is None:
        raise PrincipalResolutionError("No principal directory is bound")
    try:
        return _uuid_resolver(principal_uuid)
    except PrincipalResolutionError:
        raise
    except Exception as exc:
        raise PrincipalResolutionError("Principal unavailable") from exc
