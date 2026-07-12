"""Dedicated authentication views for isolated CTF participant accounts."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from shared.rate_limit import consume_fixed_window

if TYPE_CHECKING:
    from django.http import HttpRequest

_CTF_LOGIN_TEMPLATE = "ctf/participant/login.html"
_CTF_CHANGE_CREDENTIAL_TEMPLATE = "ctf/participant/change_password.html"


def _ctf_login_rate_limited(request: HttpRequest, username: str) -> tuple[bool, int]:
    """Charge account/source login budgets without retaining raw identifiers."""
    from django.conf import settings
    from django.core.cache import caches

    from shared.audit import get_client_ip

    window = int(getattr(settings, "CTF_LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300))
    maximum = int(getattr(settings, "CTF_LOGIN_RATE_LIMIT_MAX", 5))
    account_key = hashlib.sha256(username.strip().lower().encode()).hexdigest()[:24]
    source = str(get_client_ip(request) or "unknown")
    source_key = hashlib.sha256(source.encode()).hexdigest()[:24]
    cache = caches["launch_rate_limit"]
    counts = (
        consume_fixed_window(cache, f"ctf-login:account:{account_key}", window),
        consume_fixed_window(cache, f"ctf-login:source:{source_key}", window),
    )
    return max(counts) > maximum, window


def _login_throttle_response(request: HttpRequest, username: str) -> HttpResponse | None:
    """Return a generic throttle/failure response, or admit authentication."""
    response = None
    try:
        limited, window = _ctf_login_rate_limited(request, username)
    except Exception:
        response = render(
            request,
            _CTF_LOGIN_TEMPLATE,
            {"error": "Login is temporarily unavailable. Please retry shortly."},
            status=503,
        )
        response["Retry-After"] = "60"
    else:
        if limited:
            response = render(
                request,
                _CTF_LOGIN_TEMPLATE,
                {"error": "Invalid username or password."},
                status=429,
            )
            response["Retry-After"] = str(window)
    return response


@never_cache
@sensitive_post_parameters("password")
@require_http_methods(["GET", "POST"])
def ctf_login(request: HttpRequest) -> HttpResponse:
    """Authenticate only durably marked accounts with live participation."""
    from django.contrib.auth import login
    from django.urls import reverse

    from ctf.services import authenticate_ctf_participant

    response = None
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "")[:150]
        password = request.POST.get("password", "")
        response = _login_throttle_response(request, username)
        if response is None:
            user = authenticate_ctf_participant(request, username=username, password=password)
            if user is None:
                error = "Invalid username or password."
            else:
                login(request, user, backend="config.auth.CTFParticipantBackend")
                destination = (
                    "ctf:ctf_change_password" if user.profile.must_change_password else "ctf:participant_range"
                )
                response = redirect(reverse(destination))
    if response is None:
        response = render(request, _CTF_LOGIN_TEMPLATE, {"error": error})
    return response


@never_cache
@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@login_required
@require_http_methods(["GET", "POST"])
def ctf_change_password(request: HttpRequest) -> HttpResponse:
    """Change a temporary account password and clear the first-login gate."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    from management.services import is_temporary_ctf_account, set_ctf_password_change_required

    if not is_temporary_ctf_account(request.user):
        response = HttpResponse("Forbidden", status=403)
    else:
        form = PasswordChangeForm(request.user, request.POST or None)
        response = None
        if request.method == "POST" and form.is_valid():
            from ctf.services.participant.accounts import effective_bootstrap_password, live_participant_for_user

            participant = live_participant_for_user(request.user)
            bootstrap_reused = participant is not None and form.cleaned_data[
                "new_password1"
            ] == effective_bootstrap_password(participant.event)
            if bootstrap_reused:
                form.add_error("new_password1", "Choose a password different from the event bootstrap password.")
            else:
                user = form.save()
                set_ctf_password_change_required(user, False)
                update_session_auth_hash(request, user)
                response = redirect("ctf:participant_range")
        if response is None:
            response = render(request, _CTF_CHANGE_CREDENTIAL_TEMPLATE, {"form": form})
    assert response is not None
    return response
