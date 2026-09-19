"""Explicitly retired notification writes (ADR-065); no dispatch compatibility."""

from shared.api.errors import api_error_response


def retired_notification_response(request):
    return api_error_response(
        code="ctf_notification_retired",
        message="Legacy notification writes are retired. Use /api/v1/ctf/communications/.",
        status_code=410,
        request=request,
    )
