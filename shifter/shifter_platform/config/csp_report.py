"""Bounded same-origin CSP violation report collector (ADR-035-R3).

Transport and observability plumbing, not a business API. A POST-only,
anonymous, narrowly CSRF-exempt endpoint (browsers post reports without an
application CSRF token) that accepts the legacy ``application/csp-report`` object
and the Reporting API ``application/reports+json`` batch, bounds and sanitizes
every attacker-controlled field, emits one structured event through the existing
ECS log pipeline, and returns fixed empty responses. It performs no
authenticated or domain mutation, has no model/repository/audit/outbox, and
never returns parser exceptions, report contents, or the platform error
envelope. See ``docs/architecture/browser-security-policy-preflight-1520.md``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

# Bounds. A CSP report is small; anything larger is discarded rather than parsed.
_MAX_BODY_BYTES = 16 * 1024
_MAX_REPORTS_PER_BATCH = 50
_MAX_FIELD_LEN = 512
_MAX_DIRECTIVE_LEN = 128
_MAX_DISPOSITION_LEN = 32

_LEGACY_MEDIA = "application/csp-report"
_REPORTS_MEDIA = "application/reports+json"
_ACCEPTED_MEDIA = frozenset({_LEGACY_MEDIA, _REPORTS_MEDIA})
_CSP_REPORT_TYPE = "csp-violation"


def _origin_and_path(raw: object) -> str:
    """Return a bounded ``scheme://host/path`` with query, fragment, and
    credentials stripped. Every URL field in a report is attacker-controlled."""
    if not isinstance(raw, str) or not raw:
        return ""
    parts = urlsplit(raw)
    has_origin = bool(parts.scheme and parts.hostname)
    base = f"{parts.scheme}://{parts.hostname}{parts.path}" if has_origin else (parts.path or raw)
    return base[:_MAX_FIELD_LEN]


def _field(raw: object, max_len: int) -> str:
    """Coerce a report field to a bounded string (drops non-scalars)."""
    if raw is None or isinstance(raw, (dict, list)):
        return ""
    return str(raw)[:max_len]


def _extract_reports(payload: object, content_type: str) -> list[dict] | None:
    """Normalize either report envelope to a list of violation bodies.

    Returns ``None`` when the payload shape is not a recognized report envelope.
    """
    if content_type == _REPORTS_MEDIA:
        if not isinstance(payload, list):
            return None
        bodies = [
            item["body"]
            for item in payload
            if isinstance(item, dict) and item.get("type") == _CSP_REPORT_TYPE and isinstance(item.get("body"), dict)
        ]
        return bodies
    # Legacy application/csp-report: {"csp-report": {...}}.
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        return [payload["csp-report"]]
    return None


def _log_violation(request: HttpRequest, body: dict) -> None:
    """Emit one bounded, sanitized ECS event for a single violation."""
    directive = _field(
        body.get("effective-directive") or body.get("effectiveDirective") or body.get("violated-directive"),
        _MAX_DIRECTIVE_LEN,
    )
    disposition = _field(body.get("disposition"), _MAX_DISPOSITION_LEN)
    blocked = _origin_and_path(body.get("blocked-uri") or body.get("blockedURL"))
    document = _origin_and_path(body.get("document-uri") or body.get("documentURL"))
    logger.info(
        "csp.violation directive=%s disposition=%s blocked=%s document=%s",
        safe_log_value(directive, _MAX_DIRECTIVE_LEN),
        safe_log_value(disposition, _MAX_DISPOSITION_LEN),
        safe_log_value(blocked, _MAX_FIELD_LEN),
        safe_log_value(document, _MAX_FIELD_LEN),
        extra={
            "csp_directive": directive,
            "csp_disposition": disposition,
            "csp_blocked_origin": blocked,
            "csp_document_origin": document,
            "request": request,
        },
    )


@csrf_exempt
@require_POST
def csp_report(request: HttpRequest) -> HttpResponse:
    """Accept and log a browser CSP violation report; return a fixed empty body."""
    if request.content_type not in _ACCEPTED_MEDIA:
        return HttpResponse(status=415)
    if len(request.body) > _MAX_BODY_BYTES:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)

    reports = _extract_reports(payload, request.content_type)
    if reports is None:
        return HttpResponse(status=400)

    for body in reports[:_MAX_REPORTS_PER_BATCH]:
        _log_violation(request, body)
    return HttpResponse(status=204)
