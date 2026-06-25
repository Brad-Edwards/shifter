"""Mission Control API authentication helpers."""

from __future__ import annotations

from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """Session auth variant for the legacy upload-cancel sendBeacon endpoint.

    The pre-DRF endpoint was intentionally CSRF-exempt because
    ``navigator.sendBeacon`` cannot set the custom CSRF header on page unload.
    Keep the exception local to that endpoint instead of weakening the global
    platform DRF defaults.
    """

    def enforce_csrf(self, request: Request) -> None:
        return None
