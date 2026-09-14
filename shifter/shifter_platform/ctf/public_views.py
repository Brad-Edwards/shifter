"""Hardened public browser surface for explicitly published CTF events."""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from ctf.forms import PublicRegistrationForm
from ctf.services.public_registration import (
    PublicEventUnavailable,
    PublicRegistrationClosed,
    PublicRegistrationQueueFull,
    resolve_public_event,
    submit_public_registration_request,
)
from shared.audit import get_client_ip
from shared.rate_limit import consume_fixed_window

PUBLIC_RATE_WINDOW_SECONDS = 60 * 60
PUBLIC_SOURCE_LIMIT = 12
PUBLIC_EVENT_LIMIT = 1_000
PUBLIC_FLEET_LIMIT = 5_000
PUBLIC_MAX_BODY_BYTES = 4_096

logger = logging.getLogger(__name__)


def _private_response(response: HttpResponse) -> HttpResponse:
    """Apply the most restrictive browser metadata supported by this surface."""
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _render_public(
    request: HttpRequest,
    projection: object,
    *,
    form: PublicRegistrationForm | None = None,
    submitted: bool = False,
    status: int = 200,
) -> HttpResponse:
    return _private_response(
        render(
            request,
            "ctf/public/registration.html",
            {"event": projection, "form": form or PublicRegistrationForm(), "submitted": submitted},
            status=status,
        )
    )


def _fixed_response(message: str, *, status: int, retry_after: int | None = None) -> HttpResponse:
    response = _private_response(HttpResponse(message, status=status, content_type="text/plain; charset=utf-8"))
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


def _content_length_allowed(request: HttpRequest) -> bool:
    raw = request.META.get("CONTENT_LENGTH", "0")
    try:
        return int(raw or 0) <= PUBLIC_MAX_BODY_BYTES
    except (TypeError, ValueError):
        return False


def _source_digest(request: HttpRequest) -> str:
    source = str(get_client_ip(request) or "unknown")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _consume_public_budget(request: HttpRequest, event_id: UUID) -> bool:
    cache = caches["launch_rate_limit"]
    counters = (
        (f"ctf-public-registration:source:{event_id}:{_source_digest(request)}", PUBLIC_SOURCE_LIMIT),
        (f"ctf-public-registration:event:{event_id}", PUBLIC_EVENT_LIMIT),
        ("ctf-public-registration:fleet", PUBLIC_FLEET_LIMIT),
    )
    usage = [(consume_fixed_window(cache, key, PUBLIC_RATE_WINDOW_SECONDS), limit) for key, limit in counters]
    return all(count <= limit for count, limit in usage)


@never_cache
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def public_event_registration(request: HttpRequest, event_id: UUID) -> HttpResponse:
    """Render an allowlisted event projection or accept one pending request."""
    try:
        projection = resolve_public_event(event_id)
    except PublicEventUnavailable:
        return _fixed_response("Not found.", status=404)

    if request.method == "GET":
        return _render_public(request, projection)

    try:
        admitted = _consume_public_budget(request, event_id)
    except Exception:
        logger.exception("Public CTF registration limiter unavailable for event %s", event_id)
        return _fixed_response("Registration is temporarily unavailable.", status=503)
    if not admitted:
        return _fixed_response(
            "Too many registration attempts. Try again later.",
            status=429,
            retry_after=PUBLIC_RATE_WINDOW_SECONDS,
        )
    if not _content_length_allowed(request):
        return _fixed_response("The registration request is invalid.", status=400)

    form = PublicRegistrationForm(request.POST)
    if not form.is_valid():
        return _render_public(request, projection, form=form, status=400)
    try:
        submit_public_registration_request(
            event_id,
            name=form.cleaned_data["name"],
            email=form.cleaned_data["email"],
        )
    except PublicRegistrationClosed:
        try:
            projection = resolve_public_event(event_id)
        except PublicEventUnavailable:
            return _fixed_response("Not found.", status=404)
        return _render_public(request, projection, form=form, status=409)
    except PublicEventUnavailable:
        return _fixed_response("Not found.", status=404)
    except PublicRegistrationQueueFull:
        logger.warning("Public CTF registration queue full for event %s", event_id)
        return _fixed_response("Registration is temporarily unavailable.", status=503)
    except (ValidationError, IntegrityError):
        logger.exception("Public CTF registration persistence failed for event %s", event_id)
        return _fixed_response("Registration is temporarily unavailable.", status=503)
    return _render_public(request, projection, submitted=True)
