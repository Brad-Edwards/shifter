"""Bounded provider assurance shared by HTTP and Channels session admission."""

from __future__ import annotations

import math
import time

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, logout

from shared.principal_port import PrincipalResolutionError, principal_for_user

IDENTITY_BACKEND = "config.identity_platform.IdentityPlatformBackend"
ASSURANCE_KEY = "identity_assurance"


def establish_identity_session(session, claims: dict, *, now: float | None = None) -> None:
    """Persist only verified identity and timing evidence, never the ID token."""
    timestamp = time.time() if now is None else now
    session[ASSURANCE_KEY] = {
        "issuer": claims["iss"],
        "subject": claims["sub"],
        "auth_time": claims["auth_time"],
        "created": claims["auth_time"],
        "tenant": claims.get("tenant", ""),
        "seen": timestamp,
        "checked": timestamp,
    }


def _identity_session_valid(user, session, now: float) -> bool:
    from config.identity_platform import provider_user_state
    from management.services import resolve_user_by_provider_identity

    state = session.get(ASSURANCE_KEY)
    if not isinstance(state, dict):
        return False
    expected_issuer = f"https://securetoken.google.com/{settings.IDENTITY_PLATFORM_PROJECT_ID}"
    if state.get("issuer") != expected_issuer or state.get("tenant") != settings.IDENTITY_PLATFORM_TENANT_ID:
        return False
    for key in ("created", "seen", "checked", "auth_time"):
        value = state.get(key)
        if type(value) not in (float, int) or not math.isfinite(value) or value > now or value < 0:
            return False
    if now - state["created"] >= settings.IDENTITY_SESSION_ABSOLUTE_SECONDS:
        return False
    if now - state["seen"] >= settings.IDENTITY_SESSION_IDLE_SECONDS:
        return False
    if not resolve_user_by_provider_identity(state.get("issuer"), state.get("subject")).filter(pk=user.pk).exists():
        return False
    if now - state["checked"] >= settings.IDENTITY_SESSION_RECHECK_SECONDS:
        try:
            record = provider_user_state(state["subject"])
            if record.disabled or record.email_verified is not True:
                return False
            if record.tokens_valid_after_timestamp / 1000 > state["auth_time"]:
                return False
        except Exception:
            # Provider outage never extends the configured stale-proof window.
            return False
        state["checked"] = now
    state["seen"] = now
    session[ASSURANCE_KEY] = state
    return True


def validate_session(user, session, *, now: float | None = None) -> bool:
    """Recheck local lifecycle every use and provider state at a bounded interval."""
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        principal_for_user(user)
    except PrincipalResolutionError:
        return False
    if session.get(BACKEND_SESSION_KEY) != IDENTITY_BACKEND:
        return True
    return _identity_session_valid(user, session, time.time() if now is None else now)


def validate_socket_session(scope) -> bool:
    """Reload Django state so logout/password invalidation also closes sockets."""
    from importlib import import_module
    from types import SimpleNamespace

    from django.contrib.auth import get_user

    cached = scope.get("session")
    if cached is None or not cached.session_key:
        return False
    try:
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=cached.session_key)
        user = get_user(SimpleNamespace(session=session))
        if not validate_session(user, session):
            return False
        if session.modified:
            session.save(must_create=False)
    except Exception:
        return False
    scope["user"] = user
    scope["session"] = session
    return True


class CredentialSessionMiddleware:
    """Discard invalid sessions before any HTTP authorization boundary runs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not validate_session(request.user, request.session):
            logout(request)
        return self.get_response(request)
